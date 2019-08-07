#!/usr/bin/env python

import distutils.spawn
import json
import os
from subprocess import check_call,check_output
from sys import exit
from time import sleep

# Before running the script, fill in appropriate values for all the parameters
# above the dashed line.

# Fill in the `contexts` map with the zones of your clusters and their
# corresponding kubectl context names.
#
# To get the names of your kubectl "contexts" for each of your clusters, run:
#   kubectl config get-contexts
#
# example:
# contexts = {
#     'us-central1-a': 'gke_cockroach-alex_us-central1-a_my-cluster',
#     'us-central1-b': 'gke_cockroach-alex_us-central1-b_my-cluster',
#     'us-west1-b': 'gke_cockroach-alex_us-west1-b_my-cluster',
# }
contexts = {
    'dc-sf-1': 'default/cluster-bitski-prod-dc-sf-1-outtherelabs-com:8443/patrick@outtherelabs.com',
    'dc-sj-1': 'default/api-bitski-prod-dc-sj-1-outtherelabs-com:6443/patrick@outtherelabs.com',
    'us-west-2': 'default/console-bitski-prod-us-west-2-outtherelabs-com:8443/patrick@outtherelabs.com',
}

domains = {
    'dc-sf-1': 'apps.bitski-prod-dc-sf-1.outtherelabs.com',
    'dc-sj-1': 'apps.bitski-prod.dc-sj-1.outtherelabs.com',
    'us-west-2': 'apps.bitski-prod-us-west-2.outtherelabs.com',
}

replicas = {
  'dc-sf-1': 1,
  'dc-sj-1': 2,
  'us-west-2': 2,
}

annotations = {
  'dc-sj-1': 'openshift.io/node-selector=node-role.kubernetes.io/db=',
}

storage = {
  'dc-sf-1': 'emptydir',
  'dc-sj-1': 'hostpath',
  'us-west-2': 'pvc',
}

namespace = "db"

# Paths to directories in which to store certificates and generated YAML files.
certs_dir = './certs'
ca_key_dir = './my-safe-directory'
generated_files_dir = './generated'

# Path to the cockroach binary on your local machine that you want to use
# generate certificates. Defaults to trying to find cockroach in your PATH.
cockroach_path = 'cockroach'

# ------------------------------------------------------------------------------

# Set up the necessary directories and certificates. Ignore errors because they may already exist.
try:
    os.mkdir(certs_dir)
except OSError:
    pass
try:
    os.mkdir(ca_key_dir)
except OSError:
    pass
try:
    os.mkdir(generated_files_dir)
except OSError:
    pass

check_call([cockroach_path, 'cert', 'create-ca', '--certs-dir', certs_dir, '--ca-key', ca_key_dir+'/ca.key'])
check_call([cockroach_path, 'cert', 'create-client', 'root', '--certs-dir', certs_dir, '--ca-key', ca_key_dir+'/ca.key'])

for zone, context in contexts.items():
    domain = domains[zone]
    check_call(['oc', 'create', 'namespace', namespace, '--context', context])

    if annotations.has_key(zone)==1:      
      check_call(['oc', 'annotate', 'namespace', namespace, '--context', context, annotations[zone]])

    # check_call(['oc', 'adm', 'policy', 'add-scc-to-user', '-z', 'default', 'anyuid', '--namespace', namespace, '--context', context])
    check_call(['oc', 'create', 'secret', 'generic', 'cockroachdb.client.root', '--namespace', namespace, '--from-file', certs_dir, '--context', context])
    check_call([cockroach_path, 'cert', 'create-node', '--certs-dir', certs_dir, '--ca-key', ca_key_dir+'/ca.key', 'localhost', '127.0.0.1', 'cockroachdb-public', 'cockroachdb-public.default' 'cockroachdb-public.'+namespace, 'cockroachdb-public.%s.svc.cluster.local' % (namespace), '*.cockroachdb', '*.cockroachdb.'+namespace, '*.cockroachdb.%s.svc.cluster.local' % (namespace), '*.%s' % (domain)])
    check_call(['oc', 'create', 'secret', 'generic', 'cockroachdb.node', '--namespace', namespace, '--from-file', certs_dir, '--context', context])
    check_call('rm %s/node.*' % (certs_dir), shell=True)

# Generate the join string to be used.
join_addrs = []
for zone, context in contexts.items():
    for i in range(replicas[zone]):
        config_filename = '%s/pod-service-%i-%s.yaml' % (generated_files_dir, i, zone)
        pod_name = 'cockroachdb-%d' % (i)
        with open(config_filename, 'w') as f:
            f.write("""\
apiVersion: v1
kind: Service
metadata:
  # This service only exists to create DNS entries for each pod in the stateful
  # set such that they can resolve each other's IP addresses. It does not
  # create a load-balanced ClusterIP and should not be used directly by clients
  # in most circumstances.
  name: %s
  namespace: %s
  labels:
    app: cockroachdb
  annotations:
    service.alpha.kubernetes.io/tolerate-unready-endpoints: "true"
spec:
  ports:
  - port: 26257
    targetPort: 26257
    name: grpc
  - port: 8080
    targetPort: 8080
    name: http
  publishNotReadyAddresses: true
  clusterIP: None
  selector:
    statefulset.kubernetes.io/pod-name: %s
---
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: %s
  namespace: %s
spec:
  port:
    targetPort: grpc
  tls:
    insecureEdgeTerminationPolicy: Redirect
    termination: passthrough
  to:
    kind: Service
    name: %s
    weight: 100
  wildcardPolicy: None
""" % (pod_name,namespace,pod_name,pod_name,namespace,pod_name))
        check_call(['oc', 'apply', '-f', config_filename, '--namespace', namespace, '--context', context])
        hostname = ''
        while True:
            hostname = check_output(['oc', 'get', 'route', pod_name, '--namespace', namespace, '--context', context, '--template', '{{.spec.host}}'])
            if hostname:
                break
            print  'Waiting for route hostname for %s' % (pod_name)
            sleep(10)
        join_addrs.append("%s:443" % (hostname))
join_str = ','.join(join_addrs)

print ('Join string is %s' % (join_str))

# Create the cockroach resources in each cluster.
for zone, context in contexts.items():
    domain = domains[zone]
    locality = 'region=%s' % (zone)
    yaml_file = '%s/cockroachdb-statefulset-%s.yaml' % (generated_files_dir, zone)
    with open(yaml_file, 'w') as f:
        check_call(['sed', 's/NAMESPACE/%s/g;s/DOMAIN/%s/g;s/JOINLIST/%s/g;s/LOCALITYLIST/%s/g' % (namespace, domain, join_str, locality), 'cockroachdb-statefulset-secure-' + storage[zone] + '.yaml'], stdout=f)
    check_call(['kubectl', 'apply', '-f', yaml_file, '--namespace', namespace, '--context', context])
    sleep(3)
    check_call(['oc', 'scale', 'statefulset', 'cockroachdb', '--namespace', namespace, '--context', context, '--replicas', '%d' % (replicas[zone])])  

# Finally, initialize the cluster.
print 'Sleeping 30 seconds before attempting to initialize cluster to give time for volumes to be created and pods started.'
sleep(30)
for zone, context in contexts.items():
    check_call(['kubectl', 'create', '-f', 'cluster-init-secure.yaml', '--namespace', namespace, '--context', context])
    # We only need run the init command in one zone given that all the zones are
    # joined together as one cluster.
    break
