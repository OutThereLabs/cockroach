// Copyright 2019 The Cockroach Authors.
//
// Use of this software is governed by the Business Source License included
// in the file licenses/BSL.txt and at www.mariadb.com/bsl11.
//
// Change Date: 2022-10-01
//
// On the date above, in accordance with the Business Source License, use
// of this software will be governed by the Apache License, Version 2.0,
// included in the file licenses/APL.txt and at
// https://www.apache.org/licenses/LICENSE-2.0

package pgerror_test

import (
	"testing"

	"github.com/cockroachdb/cockroach/pkg/roachpb"
	"github.com/cockroachdb/cockroach/pkg/sql/pgwire/pgerror"
	"github.com/pkg/errors"
)

func TestWrap(t *testing.T) {
	testData := []struct {
		err        error
		expectWrap bool
	}{
		{errors.New("woo"), true},
		{&roachpb.TransactionRetryWithProtoRefreshError{}, false},
		{&roachpb.AmbiguousResultError{}, false},
	}

	for i, test := range testData {
		werr := pgerror.Wrap(test.err, pgerror.CodeSyntaxError, "woo")

		if !test.expectWrap {
			oerr := errors.Cause(werr)
			if oerr != test.err {
				t.Errorf("%d: original error not preserved; expected %+v, got %+v", i, test.err, oerr)
			}
		} else {
			_, ok := pgerror.GetPGCause(werr)
			if !ok {
				t.Errorf("%d: original error not wrapped", i)
			}
		}
	}
}
