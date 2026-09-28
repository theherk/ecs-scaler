import json
import sys

import boto3
import pytest
from botocore.exceptions import NoRegionError
from botocore.stub import Stubber

import scale

REGION = "eu-north-1"
C = f"arn:aws:ecs:{REGION}:1:cluster/"
CL = {
    "app-uat-billing": [
        "billing-auth-svc",
        "billing-erp-svc",
    ],
    "app-uat": ["prometheus-svc", "accounts-service-svc"],
}
DESIRED = {
    "prometheus-svc": 1,
    "accounts-service-svc": 2,
    "billing-auth-svc": 1,
    "billing-erp-svc": 1,
}


@pytest.fixture
def stubs(monkeypatch, capsys):
    """Stubbed clients with nothing queued; see aws for queued discovery."""
    ecs = boto3.client("ecs", region_name=REGION)
    aas = boto3.client("application-autoscaling", region_name=REGION)
    se, sa = Stubber(ecs), Stubber(aas)
    # Region must come from the client, not the environment.
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(
        boto3, "client", lambda name, **_: ecs if name == "ecs" else aas
    )

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["scale", *argv])
        se.activate()
        sa.activate()
        code = 0
        try:
            scale.main()
        except SystemExit as exc:
            code = exc.code
        se.assert_no_pending_responses()
        sa.assert_no_pending_responses()
        out = capsys.readouterr()
        return code, out.out, out.err

    return se, sa, run


@pytest.fixture
def aws(stubs):
    """Stubbed clients with list_clusters and list_services queued."""
    se, _, _ = stubs
    se.add_response("list_clusters", {"clusterArns": [C + c for c in CL]})
    for c, svcs in CL.items():
        se.add_response(
            "list_services",
            {"serviceArns": [f"arn:aws:ecs:{REGION}:1:service/{c}/{s}" for s in svcs]},
            {"cluster": C + c},
        )
    return stubs


def describe(se, names=None, overrides=None, post=False, fail=()):
    """Queue describe_services responses, grouped like ServiceManager does.

    names limits to the given services (all if None). The pre-scale call is
    over in-scope services sorted by resource id; post=True mirrors the
    post-scale call over matched services in discovery order. Clusters in
    fail get an AccessDenied error instead of a response.
    """
    overrides = overrides or {}
    ids = [f"service/{c}/{s}" for c, svcs in CL.items() for s in svcs]
    if names is not None:
        ids = [i for i in ids if i.rsplit("/", 1)[1] in names]
    if not post:
        ids.sort()
    groups = {}
    for i in ids:
        _, c, s = i.split("/")
        groups.setdefault(c, []).append(s)
    for c, sel in groups.items():
        if c in fail:
            se.add_client_error(
                "describe_services",
                "AccessDeniedException",
                "no",
                expected_params={"cluster": c, "services": sel},
            )
            continue
        se.add_response(
            "describe_services",
            {
                "services": [
                    {
                        "serviceName": s,
                        "desiredCount": overrides.get(s, DESIRED[s]),
                        "runningCount": overrides.get(s, DESIRED[s]),
                    }
                    for s in sel
                ]
            },
            {"cluster": c, "services": sel},
        )


def register(sa, svc, lo, hi):
    sa.add_response(
        "register_scalable_target",
        {},
        {
            "ServiceNamespace": "ecs",
            "ResourceId": svc,
            "ScalableDimension": "ecs:service:DesiredCount",
            "MinCapacity": lo,
            "MaxCapacity": hi,
        },
    )


def records(stdout):
    """Parse json-mode stdout; every line must be a JSON object."""
    recs = [json.loads(line) for line in stdout.splitlines()]
    assert recs and recs[-1]["type"] == "result"
    assert all(r["type"] == "service" for r in recs[:-1])
    return {r["service"]: r for r in recs[:-1]}, recs[-1]


def counts(rec):
    return [rec["previous_desired"], rec["new_desired"], rec["running"]]


def test_scale_with_excludes_json(aws):
    se, sa, run = aws
    describe(se)
    register(sa, "service/app-uat/prometheus-svc", 0, 0)
    register(sa, "service/app-uat/accounts-service-svc", 0, 0)
    describe(
        se,
        ["prometheus-svc", "accounts-service-svc"],
        {"prometheus-svc": 0, "accounts-service-svc": 2},
        post=True,
    )
    code, out, err = run(
        "uat",
        "-e", "billing-auth-svc",
        "-e", "billing-erp-svc",
        "--min", "0", "--max", "0", "--output", "json",
    )  # fmt: skip
    assert code == 0
    svcs, result = records(out)
    assert result == {
        "type": "result", "env": "uat", "region": REGION, "min": 0, "max": 0,
        "dry_run": False,
        "filters": [
            "-e billing-auth-svc",
            "-e billing-erp-svc",
        ],
        "ok": True, "error": None,
    }  # fmt: skip
    assert svcs["prometheus-svc"] == {
        "type": "service", "cluster": "app-uat", "service": "prometheus-svc",
        "previous_desired": 1, "new_desired": 0, "running": 0,
        "status": "scaled", "reason": None,
    }  # fmt: skip
    acc = svcs["accounts-service-svc"]
    assert counts(acc) == [2, 0, 2]
    assert (acc["status"], acc["reason"]) == ("converging", "desired 2 now")
    skipped = svcs["billing-auth-svc"]
    assert (skipped["status"], skipped["reason"]) == ("skipped", "excluded (-e)")
    # Progress is logged to stderr only.
    assert "service/app-uat/prometheus-svc: scale to 0/0" in err


def test_dry_run_does_not_scale(aws):
    se, _, run = aws
    describe(se, ["billing-erp-svc"])
    code, out, _ = run(
        "uat", "-i", "billing-erp-svc",
        "--min", "2", "--max", "3", "-l", "--output", "json",
    )  # fmt: skip
    assert code == 0
    svcs, result = records(out)
    assert result["dry_run"] is True and result["ok"] is True
    rec = svcs["billing-erp-svc"]
    assert counts(rec) == [1, 2, 1]
    assert rec["status"] == "dry-run"
    # Services outside the include list are not reported.
    assert set(svcs) == {"billing-erp-svc"}


def test_unknown_exclude_reports_error(aws):
    _, _, run = aws
    code, out, err = run(
        "uat", "-e", "nope-svc", "--min", "0", "--max", "0", "--output", "json"
    )
    assert code == 1
    svcs, result = records(out)
    assert svcs == {}
    assert result["ok"] is False
    assert "nope-svc not found" in result["error"]
    assert "nope-svc not found" in err


def test_per_service_failure_continues_and_exits_1(aws):
    se, sa, run = aws
    describe(se, ["prometheus-svc", "accounts-service-svc"])
    sa.add_client_error("register_scalable_target", "AccessDeniedException", "denied")
    register(sa, "service/app-uat/accounts-service-svc", 1, 1)
    # Post-scale counts cover every matched service, including failures.
    describe(
        se,
        ["prometheus-svc", "accounts-service-svc"],
        {"accounts-service-svc": 1},
        post=True,
    )
    code, out, _ = run(
        "uat", "-i", "accounts-service-svc", "-i", "prometheus-svc",
        "--min", "1", "--max", "1", "--output", "json",
    )  # fmt: skip
    assert code == 1
    svcs, result = records(out)
    assert result["ok"] is False and result["error"] is None
    failed = svcs["prometheus-svc"]
    assert counts(failed)[:2] == [1, 1]
    assert failed["status"] == "failed" and "denied" in failed["reason"]
    assert counts(svcs["accounts-service-svc"]) == [2, 1, 1]
    assert svcs["accounts-service-svc"]["status"] == "scaled"


def test_describe_denied_still_scales(aws):
    se, sa, run = aws
    se.add_client_error("describe_services", "AccessDeniedException", "no")
    register(sa, "service/app-uat/prometheus-svc", 1, 1)
    se.add_client_error("describe_services", "AccessDeniedException", "no")
    code, out, err = run(
        "uat", "-i", "prometheus-svc", "--min", "1", "--max", "1", "--output", "json"
    )
    assert code == 0
    svcs, _ = records(out)
    assert counts(svcs["prometheus-svc"]) == [None, None, None]
    assert svcs["prometheus-svc"]["status"] == "scaled"
    assert "could not describe services" in err


# Text mode makes no describe_services calls; the Stubber fails the test on
# any unqueued call (surfacing as a warning on stderr or a nonzero exit).


def test_text_mode_output_unchanged(aws):
    _, sa, run = aws
    register(sa, "service/app-uat/prometheus-svc", 1, 1)
    code, out, err = run("uat", "-i", "prometheus-svc", "--min", "1", "--max", "1")
    assert (code, err) == (0, "")
    assert out == "service/app-uat/prometheus-svc: scale to 1/1\n"


def test_text_mode_dry_run_unchanged(aws):
    _, _, run = aws
    code, out, err = run("uat", "-i", "prometheus-svc", "-l")
    assert (code, err) == (0, "")
    assert out == "matched services:\n\tservice/app-uat/prometheus-svc\n"


def test_text_mode_failure_continues_without_describe(aws):
    _, sa, run = aws
    sa.add_client_error("register_scalable_target", "AccessDeniedException", "denied")
    register(sa, "service/app-uat/accounts-service-svc", 1, 1)
    code, out, err = run(
        "uat", "-i", "accounts-service-svc", "-i", "prometheus-svc",
        "--min", "1", "--max", "1",
    )  # fmt: skip
    assert code == 1
    assert out.splitlines() == [
        "service/app-uat/prometheus-svc: scale to 1/1",
        "service/app-uat/accounts-service-svc: scale to 1/1",
    ]
    assert err.startswith("service/app-uat/prometheus-svc: failed: ")
    assert "could not describe" not in err


def test_text_mode_unknown_filter(aws):
    _, _, run = aws
    code, out, _ = run("uat", "-i", "nope-svc")
    assert code == 1
    assert out.startswith("include: nope-svc not found in [")


def test_partial_counts_survive_failed_batch(aws):
    se, _, run = aws
    describe(se, fail={"app-uat-billing"})
    code, out, err = run("uat", "-l", "--output", "json")
    assert code == 0
    svcs, _ = records(out)
    assert counts(svcs["prometheus-svc"]) == [1, 1, 1]
    assert counts(svcs["accounts-service-svc"]) == [2, 2, 2]
    assert counts(svcs["billing-auth-svc"]) == [None, None, None]
    assert "could not describe services in app-uat-billing" in err


def test_region_from_client_not_env(aws):
    se, _, run = aws
    describe(se, ["prometheus-svc"])
    code, out, _ = run("uat", "-i", "prometheus-svc", "-l", "--output", "json")
    assert code == 0
    _, result = records(out)
    # stubs sets AWS_DEFAULT_REGION=us-east-1; the client says eu-north-1.
    assert result["region"] == REGION


def test_list_clusters_denied_json(stubs):
    se, _, run = stubs
    se.add_client_error("list_clusters", "AccessDeniedException", "nope")
    code, out, err = run("uat", "--output", "json")
    assert code == 1
    svcs, result = records(out)
    assert svcs == {}
    assert result["ok"] is False and "nope" in result["error"]
    assert result["region"] == REGION
    assert "Traceback" not in err


def test_list_services_denied_text(stubs):
    se, _, run = stubs
    se.add_response("list_clusters", {"clusterArns": [C + c for c in CL]})
    se.add_client_error("list_services", "AccessDeniedException", "nope")
    code, out, err = run("uat")
    assert code == 1
    assert out == ""
    assert err.startswith("error: ") and "nope" in err
    assert "Traceback" not in err
    assert len(err.splitlines()) == 1


def test_client_creation_failure_json(stubs, monkeypatch):
    _, _, run = stubs

    def fail(*_, **__):
        raise NoRegionError()

    monkeypatch.setattr(boto3, "client", fail)
    code, out, err = run("uat", "--output", "json")
    assert code == 1
    _, result = records(out)
    assert result["ok"] is False
    assert result["error"] == "You must specify a region."
    assert result["region"] is None
    assert err == "error: You must specify a region.\n"


@pytest.mark.parametrize(
    "argv, msg",
    [
        (["--min", "-1"], "must be non-negative"),
        (["--max", "-1"], "must be non-negative"),
        (["--min", "3", "--max", "2"], "--min (3) must not exceed --max (2)"),
    ],
)
def test_invalid_capacity_is_usage_error(stubs, argv, msg):
    _, _, run = stubs
    code, out, err = run("uat", *argv, "--output", "json")
    assert code == 2
    assert out == ""
    assert msg in err
