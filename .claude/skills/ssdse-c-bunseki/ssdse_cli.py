#!/usr/bin/env python3
"""SSDSE-C-2026 都市別家計支出データ分析 CLI."""

from __future__ import annotations

import argparse
import csv
import sys
import unicodedata
import urllib.request
from pathlib import Path

DEFAULT_CSV = Path(__file__).resolve().parent / "SSDSE-C-2026.csv"
CSV_URL = "https://www.nstac.go.jp/files/SSDSE-C-2026.csv"
META_COLS = 4
NATIONAL_CODE = "R00000"


def ensure_csv(csv_path: Path) -> None:
    if csv_path.exists():
        return
    print(f"[INFO] CSV が見つかりません。{CSV_URL} からダウンロードします...", file=sys.stderr)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        CSV_URL,
        headers={"User-Agent": "Mozilla/5.0 (ssdse-c-bunseki skill)"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
    except Exception as e:
        raise SystemExit(f"[ERROR] CSV のダウンロードに失敗しました: {e}")
    csv_path.write_bytes(data)
    print(f"[INFO] 保存しました: {csv_path} ({len(data):,} bytes)", file=sys.stderr)


def disp_w(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, width: int, align: str) -> str:
    diff = width - disp_w(s)
    if diff <= 0:
        return s
    return " " * diff + s if align == "right" else s + " " * diff


def render_table(headers: list[str], rows: list[list[str]], aligns: list[str] | None = None) -> str:
    if not rows and not headers:
        return ""
    aligns = aligns or ["left"] * len(headers)
    widths = [disp_w(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], disp_w(cell))
    sep = "  "
    lines = [sep.join(pad(h, widths[i], aligns[i]) for i, h in enumerate(headers))]
    lines.append(sep.join("-" * widths[i] for i in range(len(headers))))
    for r in rows:
        lines.append(sep.join(pad(cell, widths[i], aligns[i]) for i, cell in enumerate(r)))
    return "\n".join(lines)


class Dataset:
    def __init__(self, csv_path: Path):
        with open(csv_path, encoding="cp932", newline="") as f:
            rows = list(csv.reader(f))
        if len(rows) < 3:
            raise ValueError(f"CSV にデータ行がありません: {csv_path}")
        self.codes = rows[0]
        self.names = rows[1]
        self.cities = [
            {"code": r[0], "pref": r[1], "city": r[2], "values": r}
            for r in rows[2:]
        ]
        self.code_to_col = {c: i for i, c in enumerate(self.codes)}
        self.national = next((c for c in self.cities if c["code"] == NATIONAL_CODE), None)

    def find_cities(self, query: str) -> list[dict]:
        exact = [c for c in self.cities if query in (c["code"], c["pref"], c["city"])]
        if exact:
            return exact
        return [c for c in self.cities if query in c["pref"] or query in c["city"] or query in c["code"]]

    def find_items(self, query: str) -> list[int]:
        exact, partial = [], []
        for i in range(META_COLS, len(self.codes)):
            code, name = self.codes[i], self.names[i]
            if query == code or query == name:
                exact.append(i)
            elif query in code or query in name:
                partial.append(i)
        return exact or partial

    def value(self, city: dict, col: int) -> float | None:
        v = city["values"][col]
        if v in ("", "-"):
            return None
        try:
            return float(v)
        except ValueError:
            return None

    def label(self, col: int) -> str:
        return f"{self.names[col]} [{self.codes[col]}]"

    def category_cols(self) -> list[int]:
        return [
            i for i in range(META_COLS, len(self.codes))
            if self.codes[i].startswith("LB") and len(self.codes[i]) == 4 and self.codes[i] != "LB00"
        ]

    def leaf_cols(self) -> list[int]:
        return [
            i for i in range(META_COLS, len(self.codes))
            if self.codes[i].startswith("LB") and len(self.codes[i]) > 4
        ]


def resolve_city(ds: Dataset, query: str) -> dict | None:
    matches = ds.find_cities(query)
    if not matches:
        print(f"[該当なし] 都市 '{query}' が見つかりません。", file=sys.stderr)
        return None
    if len(matches) > 1:
        print(f"[曖昧] '{query}' に複数の都市が一致しました:", file=sys.stderr)
        for m in matches:
            print(f"  {m['code']}  {m['pref']}  {m['city']}", file=sys.stderr)
        return None
    return matches[0]


def resolve_item(ds: Dataset, query: str, allow_multi: bool = False) -> int | list[int] | None:
    cols = ds.find_items(query)
    if not cols:
        print(f"[該当なし] 品目 '{query}' が見つかりません。", file=sys.stderr)
        return None
    if len(cols) == 1:
        return cols[0] if not allow_multi else cols
    if allow_multi:
        return cols
    print(f"[曖昧] '{query}' に {len(cols)} 件の品目が一致しました:", file=sys.stderr)
    for c in cols[:15]:
        print(f"  {ds.codes[c]:<10} {ds.names[c]}", file=sys.stderr)
    if len(cols) > 15:
        print(f"  ... ほか {len(cols) - 15} 件", file=sys.stderr)
    print("コードを直接指定するか、より具体的なキーワードを使ってください。", file=sys.stderr)
    return None


def cmd_search(ds: Dataset, args) -> int:
    cols = ds.find_items(args.keyword)
    if not cols:
        print(f"'{args.keyword}' に一致する品目はありません。")
        return 1
    rows = [[ds.codes[c], ds.names[c]] for c in cols]
    print(render_table(["コード", "品目名"], rows))
    print(f"\n{len(cols)} 件ヒット")
    return 0


def cmd_rank(ds: Dataset, args) -> int:
    col = resolve_item(ds, args.item)
    if col is None or isinstance(col, list):
        return 1

    pairs = []
    for c in ds.cities:
        if c["code"] == NATIONAL_CODE:
            continue
        v = ds.value(c, col)
        if v is not None:
            pairs.append((c, v))
    pairs.sort(key=lambda p: p[1], reverse=not args.bottom)

    nat_v = ds.value(ds.national, col) if ds.national else None
    print(f"品目: {ds.label(col)}")
    if nat_v is not None:
        print(f"全国平均: {nat_v:,.0f}")
    print(f"{'下位' if args.bottom else '上位'} {args.top} 都市:\n")

    rows = []
    for i, (c, v) in enumerate(pairs[: args.top], 1):
        ratio = f"{v / nat_v * 100:.1f}%" if nat_v else "-"
        rows.append([str(i), f"{c['pref']} {c['city']}", f"{v:,.0f}", ratio])
    print(render_table(["順位", "都市", "値", "全国比"], rows, ["right", "left", "right", "right"]))
    return 0


def cmd_compare(ds: Dataset, args) -> int:
    cities = []
    for q in args.cities:
        c = resolve_city(ds, q)
        if c is None:
            return 1
        cities.append(c)

    if args.items:
        cols = []
        for q in args.items:
            r = resolve_item(ds, q)
            if r is None or isinstance(r, list):
                return 1
            cols.append(r)
    else:
        cols = [ds.code_to_col["LB00"]] + ds.category_cols()

    headers = ["品目"] + [f"{c['pref']} {c['city']}" for c in cities]
    aligns = ["left"] + ["right"] * len(cities)
    rows = []
    for col in cols:
        row = [ds.names[col]]
        for c in cities:
            v = ds.value(c, col)
            row.append(f"{v:,.0f}" if v is not None else "-")
        rows.append(row)

    print(render_table(headers, rows, aligns))
    return 0


def cmd_profile(ds: Dataset, args) -> int:
    city = resolve_city(ds, args.city)
    if city is None:
        return 1
    nat = ds.national

    print(f"=== {city['pref']} {city['city']} ({city['code']}) ===")
    members = city["values"][3]
    print(f"世帯人員: {members} 人")
    food_col = ds.code_to_col["LB00"]
    food = ds.value(city, food_col)
    if food is not None:
        line = f"食料合計: {food:,.0f} 円/年"
        if nat:
            nv = ds.value(nat, food_col)
            if nv:
                line += f"  (全国比 {food / nv * 100:.1f}%)"
        print(line)
    print()

    print("【大分類別 支出】")
    rows = []
    for col in ds.category_cols():
        v = ds.value(city, col)
        nv = ds.value(nat, col) if nat else None
        ratio = f"{v / nv * 100:.1f}%" if v is not None and nv else "-"
        rows.append([
            ds.names[col],
            f"{v:,.0f}" if v is not None else "-",
            f"{nv:,.0f}" if nv is not None else "-",
            ratio,
        ])
    print(render_table(["分類", "当市", "全国", "全国比"], rows,
                       ["left", "right", "right", "right"]))
    print()

    devs = []
    for col in ds.leaf_cols():
        v = ds.value(city, col)
        nv = ds.value(nat, col) if nat else None
        if v is None or nv is None or nv == 0 or nv < args.min_value:
            continue
        devs.append((col, v, nv, v / nv))

    print(f"【全国比が高い品目 上位 {args.top}】 (全国 {args.min_value:,} 円以上)")
    devs.sort(key=lambda x: x[3], reverse=True)
    rows = [
        [ds.names[c], f"{v:,.0f}", f"{nv:,.0f}", f"{r * 100:.1f}%"]
        for c, v, nv, r in devs[: args.top]
    ]
    print(render_table(["品目", "当市", "全国", "全国比"], rows,
                       ["left", "right", "right", "right"]))
    print()

    print(f"【全国比が低い品目 下位 {args.top}】 (全国 {args.min_value:,} 円以上)")
    devs.sort(key=lambda x: x[3])
    rows = [
        [ds.names[c], f"{v:,.0f}", f"{nv:,.0f}", f"{r * 100:.1f}%"]
        for c, v, nv, r in devs[: args.top]
    ]
    print(render_table(["品目", "当市", "全国", "全国比"], rows,
                       ["left", "right", "right", "right"]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ssdse_cli",
        description="SSDSE-C-2026 (都市別家計支出データ) 分析 CLI",
    )
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV, help=f"CSV パス (既定: {DEFAULT_CSV})")
    sp = p.add_subparsers(dest="command", required=True)

    s = sp.add_parser("search", help="品目名検索")
    s.add_argument("keyword", help="部分一致するキーワード")

    r = sp.add_parser("rank", help="品目別の都市ランキング")
    r.add_argument("item", help="品目名 または LB コード")
    r.add_argument("--top", type=int, default=10, help="表示件数 (既定 10)")
    r.add_argument("--bottom", action="store_true", help="下位ランキングを表示")

    c = sp.add_parser("compare", help="都市の支出比較")
    c.add_argument("cities", nargs="+", help="都市名 (2 つ以上)")
    c.add_argument("--items", nargs="*", help="品目を指定 (省略時は大分類の集計値)")

    pf = sp.add_parser("profile", help="都市プロファイル出力")
    pf.add_argument("city", help="都市名")
    pf.add_argument("--top", type=int, default=10, help="特徴品目の表示件数 (既定 10)")
    pf.add_argument("--min-value", type=int, default=1000,
                    help="特徴品目の対象とする全国平均の最小金額 (既定 1000)")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ensure_csv(args.csv)
    ds = Dataset(args.csv)
    handlers = {
        "search": cmd_search,
        "rank": cmd_rank,
        "compare": cmd_compare,
        "profile": cmd_profile,
    }
    return handlers[args.command](ds, args)


if __name__ == "__main__":
    sys.exit(main())
