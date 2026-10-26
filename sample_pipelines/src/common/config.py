"""Job arguments and table names. Nothing here is hard-coded per environment."""

from __future__ import annotations

import argparse
from dataclasses import dataclass


@dataclass(frozen=True)
class Tables:
    catalog: str
    env: str
    layout: str = "catalog"  # "catalog": <env>.bronze.x ; "schema": workspace.<env>_bronze.x

    def _schema(self, layer: str) -> str:
        if self.layout == "schema":
            return f"{self.catalog}.{self.env}_{layer}"
        return f"{self.catalog}.{layer}"

    @property
    def bronze_orders(self) -> str:
        return f"{self._schema('bronze')}.orders"

    @property
    def silver_orders(self) -> str:
        return f"{self._schema('silver')}.orders"

    @property
    def gold_daily_sales(self) -> str:
        return f"{self._schema('gold')}.daily_sales"

    @property
    def gold_sales_report(self) -> str:
        return f"{self._schema('gold')}.sales_report"

    @property
    def partner_feed(self) -> str:
        return f"{self._schema('bronze')}.partner_feed"

    @property
    def landing_path(self) -> str:
        return "/Volumes/" + self._schema("bronze").replace(".", "/") + "/landing/orders"


def parse_args(argv: list[str] | None = None, extra: callable | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--env", required=True, choices=["dev", "qa", "prod"])
    parser.add_argument("--layout", default="catalog", choices=["catalog", "schema"])
    parser.add_argument("--src-dir", help="deployed src folder; read by each script's import bootstrap")
    if extra:
        extra(parser)
    return parser.parse_args(argv)


def tables_from_args(args: argparse.Namespace) -> Tables:
    return Tables(catalog=args.catalog, env=args.env, layout=args.layout)
