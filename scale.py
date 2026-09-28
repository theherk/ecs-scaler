#!/usr/bin/env python3
import argparse
import json
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

DESCRIBE_BATCH = 10  # ecs:DescribeServices accepts at most 10 services per call.


class ServiceManagerException(Exception):
    pass


class ServiceManager:
    def __init__(self, env, includes=None, excludes=None, describe=False, out=None):
        self.env = env
        self.includes = includes or None
        self.excludes = excludes or []
        self.describe = describe  # Fetch counts via ecs:DescribeServices.
        self.out = out or sys.stdout  # Human-readable progress.
        self.__aas = None
        self.__ecs = None
        self.__names = []  # Avoid duplicate filtering.
        self.__skipped = []  # In scope but excluded with -e.
        self.results = []  # One row per service in scope, for reporting.

    def log(self, msg):
        print(msg, file=self.out)

    @property
    def region(self):
        """Region resolved by the first boto3 client created, or None."""
        for client in (self.__ecs, self.__aas):
            if client is not None:
                return client.meta.region_name
        return None

    @property
    def _aas(self):
        if self.__aas is not None:
            return self.__aas
        self.__aas = boto3.client("application-autoscaling")
        return self._aas

    def _clusters(self):
        return [
            arn
            for arn in self._ecs.list_clusters()["clusterArns"]
            if f"-{self.env}" in arn or f"{self.env}-" in arn
        ]

    @property
    def _ecs(self):
        if self.__ecs is not None:
            return self.__ecs
        self.__ecs = boto3.client("ecs")
        return self._ecs

    @staticmethod
    def _fmt_service_name(cluster_arn, service_arn):
        """Return constructed resource names.

        When calling list_services, some arn's have cluster, some don't.
        If all did, we could simply use rsplit(":", maxsplit=1)[1]. But,
        instead we must construct.

        Return:
            string: service/[cluster]/[service]
        """
        return "service/{}/{}".format(
            cluster_arn.rsplit("/", maxsplit=1)[1],
            service_arn.rsplit("/", maxsplit=1)[1],
        )

    @staticmethod
    def _split(service):
        """Return (cluster, service) from service/[cluster]/[service]."""
        _, cluster, name = service.split("/", maxsplit=2)
        return cluster, name

    def _counts(self, services):
        """Return {resource id: (desired, running)} for the given services.

        Only fetched when self.describe is set. Best effort: counts are only
        used for reporting, so a failed call (e.g. missing
        ecs:DescribeServices) is logged and its services are left out,
        without blocking scaling or discarding other batches.
        """
        if not self.describe:
            return {}
        by_cluster = {}
        for svc in services:
            cluster, name = self._split(svc)
            by_cluster.setdefault(cluster, []).append(name)
        counts = {}
        for cluster, names in by_cluster.items():
            for i in range(0, len(names), DESCRIBE_BATCH):
                try:
                    resp = self._ecs.describe_services(
                        cluster=cluster, services=names[i : i + DESCRIBE_BATCH]
                    )
                except (BotoCoreError, ClientError) as exc:
                    print(
                        f"warning: could not describe services in {cluster}: {exc}",
                        file=sys.stderr,
                    )
                    continue
                for s in resp["services"]:
                    counts[f"service/{cluster}/{s['serviceName']}"] = (
                        s["desiredCount"],
                        s["runningCount"],
                    )
        return counts

    def _scale(self, service, min, max):
        self.log(f"{service}: scale to {min}/{max}")
        return self._aas.register_scalable_target(
            ServiceNamespace="ecs",
            ResourceId=service,
            ScalableDimension="ecs:service:DesiredCount",
            MinCapacity=min,
            MaxCapacity=max,
        )

    def _filter_excludes(self, services):
        for exclude in self.excludes:
            if exclude not in self.__names:
                raise ServiceManagerException(
                    f"exclude: {exclude} not found in {self.__names}"
                )
        return [s for s in services if s.split("/")[-1] not in self.excludes]

    def _filter_includes(self, services):
        """Filter to only included applications if given. Otherwise all are return."""
        if self.includes is None:
            return services
        for include in self.includes:
            if include not in self.__names:
                raise ServiceManagerException(
                    f"include: {include} not found in {self.__names}"
                )
        return [s for s in services if s.split("/")[-1] in self.includes]

    def _services(self):
        svcs = []
        for cluster in self._clusters():
            svcs.extend(
                [
                    self._fmt_service_name(cluster, arn)
                    for arn in self._ecs.list_services(cluster=cluster)["serviceArns"]
                ]
            )
        self.__names = sorted([s.split("/")[-1] for s in svcs])
        # Services outside an include list are out of scope and not reported;
        # in-scope services removed by -e are reported as skipped.
        scoped = self._filter_includes(svcs)
        svcs = self._filter_excludes(scoped)
        self.__skipped = sorted(set(scoped) - set(svcs))
        return svcs

    def _record(self, service, prev, new, running, status, reason=None):
        cluster, name = self._split(service)
        self.results.append(
            {
                "type": "service",
                "cluster": cluster,
                "service": name,
                "previous_desired": prev,
                "new_desired": new,
                "running": running,
                "status": status,
                "reason": reason,
            }
        )

    def _record_skipped(self, before):
        for svc in self.__skipped:
            desired, running = before.get(svc, (None, None))
            self._record(svc, desired, desired, running, "skipped", "excluded (-e)")

    def list(self, min=None, max=None):
        """List matched services without scaling (dry run)."""
        matched = self._services()
        before = self._counts(sorted(matched + self.__skipped))
        self.log("matched services:")
        for svc in matched:
            self.log(f"\t{svc}")
            desired, running = before.get(svc, (None, None))
            new = _clamp(desired, min, max) if min is not None else None
            self._record(svc, desired, new, running, "dry-run")
        self._record_skipped(before)

    def scale(self, min, max):
        """Scale matched services. Return True if every service succeeded."""
        matched = self._services()
        before = self._counts(sorted(matched + self.__skipped))
        ok = True
        for svc in matched:
            desired, running = before.get(svc, (None, None))
            try:
                self._scale(svc, min, max)
            except (BotoCoreError, ClientError) as exc:
                print(f"{svc}: failed: {exc}", file=sys.stderr)
                self._record(svc, desired, desired, running, "failed", str(exc))
                ok = False
                continue
            self._record(svc, desired, None, None, "scaled")
        # Re-read after scaling; application-autoscaling clamps desired into [min, max].
        after = self._counts(matched)
        for row in self.results:
            if row["status"] != "scaled":
                continue
            svc = f"service/{row['cluster']}/{row['service']}"
            expected = _clamp(row["previous_desired"], min, max)
            desired, running = after.get(svc, (None, None))
            row["running"] = running
            row["new_desired"] = expected if expected is not None else desired
            if desired is not None and expected is not None and desired != expected:
                # Autoscaling has not converged yet; report the target it will reach.
                row["status"] = "converging"
                row["reason"] = f"desired {desired} now"
        self._record_skipped(before)
        return ok


def _clamp(value, min, max):
    if value is None:
        return None
    return sorted((min, value, max))[1]


def _emit_json(args, svc_mgr, ok, error=None):
    for row in svc_mgr.results:
        print(json.dumps(row))
    filters = [f"-i {i}" for i in args.include or []] + [
        f"-e {e}" for e in args.exclude or []
    ]
    print(
        json.dumps(
            {
                "type": "result",
                "env": args.env,
                "region": svc_mgr.region,
                "min": args.min,
                "max": args.max,
                "dry_run": bool(args.list),
                "filters": filters,
                "ok": ok,
                "error": error,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("env", help="Environment (for filtering clusters -ENV or ENV-)")
    parser.add_argument(
        "-l",
        "--list",
        action=argparse.BooleanOptionalAction,
        help="List matched services.",
    )
    parser.add_argument(
        "-i",
        "--include",
        action="append",
        help="String to match for service inclusion. Can be passed multiple times. All if none given.",
    )
    parser.add_argument(
        "-e",
        "--exclude",
        action="append",
        help="String to match for service exclusion. Can be passed multiple times.",
    )
    parser.add_argument(
        "--min",
        default=1,
        type=int,
        help="Minimum capacity. default: 1",
    )
    parser.add_argument(
        "--max",
        default=2,
        type=int,
        help="Maximum capacity. default: 2",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format. json writes JSON Lines to stdout and logs to stderr. default: text",
    )
    args = parser.parse_args()
    if args.min < 0 or args.max < 0:
        parser.error("--min and --max must be non-negative")
    if args.min > args.max:
        parser.error(f"--min ({args.min}) must not exceed --max ({args.max})")
    json_out = args.output == "json"
    # Human-readable progress goes to stdout in text mode and stderr in json
    # mode, so json stdout carries only machine-readable records. Counts are
    # only reported in json mode, so only fetch them then.
    svc_mgr = ServiceManager(
        args.env,
        args.include,
        args.exclude,
        describe=json_out,
        out=sys.stderr if json_out else sys.stdout,
    )
    error = None
    try:
        if args.list:
            svc_mgr.list(args.min, args.max)
            ok = True
        else:
            ok = svc_mgr.scale(args.min, args.max)
    except ServiceManagerException as exc:
        error = str(exc)
        ok = False
        svc_mgr.log(error)
    except (BotoCoreError, ClientError) as exc:
        # e.g. NoRegionError, NoCredentialsError, AccessDenied on discovery.
        error = str(exc)
        ok = False
        print(f"error: {error}", file=sys.stderr)
    if json_out:
        _emit_json(args, svc_mgr, ok, error)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
