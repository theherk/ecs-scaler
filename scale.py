#!/usr/bin/env python3
import argparse
import sys

import boto3


class ServiceManagerException(Exception):
    pass


class ServiceManager:
    def __init__(self, env, includes=None, excludes=None):
        self.env = env
        self.includes = includes or None
        self.excludes = excludes or []
        self.__aas = None
        self.__ecs = None
        self.__names = []  # Avoid duplicate filtering.

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

    def _scale(self, service, min, max):
        print(f"{service}: scale to {min}/{max}")
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
        svcs = self._filter_includes(svcs)
        svcs = self._filter_excludes(svcs)
        return svcs

    def list(self):
        print("matched services:")
        for svc in self._services():
            print(f"\t{svc}")

    def scale(self, min, max):
        for svc in self._services():
            self._scale(svc, min, max)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "env", help=f"Environment (for filtering clusters -ENV or ENV-)"
    )
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
    args = parser.parse_args()
    svc_mgr = ServiceManager(args.env, args.include, args.exclude)
    if args.list:
        svc_mgr.list()
        sys.exit(0)
    try:
        svc_mgr.scale(args.min, args.max)
    except ServiceManagerException as exc:
        print(exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
