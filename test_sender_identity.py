"""BR-31 tests for sender_identity.resolve_seat — Ace's implementation,
written to the semantics of Flint's BR-31 package (12 checks).

Run: python3 -m pytest test_sender_identity.py  (or plain python3).
"""
import sender_identity as si

MAP = {"ace": "tok-ace-0001", "flint": "tok-flint-0002", "shavor": "tok-shavor-0003"}


def run():
    cases = []

    def check(name, got, want):
        cases.append((name, got, want))

    check("desk token -> ace", si.resolve_seat("tok-ace-0001", MAP), "ace")
    check("flint token -> flint", si.resolve_seat("tok-flint-0002", MAP), "flint")
    check("shavor token -> shavor", si.resolve_seat("tok-shavor-0003", MAP), "shavor")
    check("unknown token -> None", si.resolve_seat("tok-nope", MAP), None)
    check("empty token -> None", si.resolve_seat("", MAP), None)
    check("None token -> None", si.resolve_seat(None, MAP), None)
    check("non-string token -> None", si.resolve_seat(12345, MAP), None)
    check("near-miss token -> None", si.resolve_seat("tok-flint-0002x", MAP), None)
    check("case differs -> None", si.resolve_seat("TOK-FLINT-0002", MAP), None)
    # Fail closed on duplicated values: flint value copied into shavor's
    # slot must resolve to NO seat (never to either identity).
    dup = {"ace": "tok-ace-0001", "flint": "tok-same", "shavor": "tok-same"}
    check("duplicated value -> None", si.resolve_seat("tok-same", dup), None)
    check("unduplicated still works", si.resolve_seat("tok-ace-0001", dup), "ace")
    # A seat whose token is unset (empty) can never be resolved into.
    unset = {"ace": "tok-ace-0001", "flint": "", "shavor": "tok-shavor-0003"}
    check("unset seat unreachable", si.resolve_seat("tok-flint-0002", unset), None)

    bad = [(n, g, w) for n, g, w in cases if g != w]
    for n, g, w in bad:
        print("FAIL:", n, "got", g, "want", w)
    print("%d/%d passed" % (len(cases) - len(bad), len(cases)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(run())
