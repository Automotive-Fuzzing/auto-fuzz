# src/cli.py

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import click

from config.loader import load_config
from .pipeline import AutoFuzzPipeline


def _load_cfg(config: Optional[str], dbc: Optional[str], db: Optional[str]) -> Dict[str, Any]:
    cfg = load_config(config)
    if dbc:
        cfg.setdefault("paths", {})["dbc"] = dbc
    if db:
        cfg.setdefault("paths", {})["seed_db"] = db
    return cfg


def _format_status(status: str) -> str:
    status = str(status).lower()
    colors = {
        "ok": "green",
        "timeout": "yellow",
        "crashed": "red",
        "skipped": "blue",
        "pending": "white",
        "confirmed_fail": "red",
        "reproduced_pass": "cyan",
        "sent": "yellow",
        "queued": "white",
    }
    return click.style(status.upper(), fg=colors.get(status, "white"))


def _print_monitor_results(results: Dict[str, Any]) -> None:
    click.echo(click.style("\n=== AutoFuzz Monitor Results ===\n", fg="cyan", bold=True))
    for name in ("timing", "uds", "dbc"):
        value = results.get(name, {})
        score = float(value.get("score", 0.0))
        status = str(value.get("status", "unknown"))
        completed = bool(value.get("completed", False))
        click.echo(
            f"{name.upper():<10} | Score: {score:>6.3f} | "
            f"Status: {_format_status(status)} | Completed: {completed}"
        )
    click.echo()


@click.group()
def cli() -> None:
    """Auto-Fuzz CLI."""


@cli.command("register")
@click.option("--config", default=None, help="YAML config path")
@click.option("--dbc", default=None, help="DBC file path override")
@click.option("--db", default=None, help="seed DB path override")
def register_cmd(config: Optional[str], dbc: Optional[str], db: Optional[str]) -> None:
    cfg = _load_cfg(config, dbc, db)
    count = AutoFuzzPipeline.register_seeds(
        dbc_path=cfg["paths"]["dbc"],
        db_path=cfg["paths"]["seed_db"],
    )
    click.echo(click.style(f"[✓] {count} seeds registered.", fg="green"))


@cli.command("list")
@click.option("--config", default=None, help="YAML config path")
@click.option("--dbc", default=None, help="DBC file path override")
@click.option("--db", default=None, help="seed DB path override")
def list_cmd(config: Optional[str], dbc: Optional[str], db: Optional[str]) -> None:
    cfg = _load_cfg(config, dbc, db)
    seeds = AutoFuzzPipeline.list_seeds(cfg["paths"]["seed_db"])

    if not seeds:
        click.echo("[!] No seeds found.")
        return

    click.echo(f"[+] {len(seeds)} seeds loaded")
    click.echo("-" * 120)
    for seed in seeds:
        arb_id = seed.arb_id if seed.arb_id is not None else seed.message_id
        verdict = seed.repro_verdict or "-"
        payload_hex = (seed.payload or b"").hex()
        click.echo(
            f"[{seed.id:03}] "
            f"arb_id={hex(int(arb_id)) if arb_id is not None else '-':<12} "
            f"dlc={seed.dlc:<2} depth={seed.depth:<2} prio={seed.priority:<2} "
            f"status={seed.status:<16} verdict={verdict:<10} payload={payload_hex}"
        )
    click.echo("-" * 120)


@cli.command("run")
@click.option("--config", default=None, help="YAML config path")
@click.option("--dbc", default=None, help="DBC file path override")
@click.option("--db", default=None, help="seed DB path override")
@click.option("--max-seeds", type=int, default=None, help="Process at most N seeds")
@click.option(
    "--save-json",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write run summary JSON to a file",
)
@click.option("--dry-run", is_flag=True, help="Disable CAN Tx and print stub output only")
def run_cmd(
    config: Optional[str],
    dbc: Optional[str],
    db: Optional[str],
    max_seeds: Optional[int],
    save_json: Optional[Path],
    dry_run: bool,
) -> None:
    cfg = _load_cfg(config, dbc, db)

    can_iface = None
    if cfg["can"].get("enable", False) and not dry_run:
        from .interface.can_interface import CANInterface

        can_iface = CANInterface(
            channel=cfg["can"]["channel"],
            can_id=int(cfg["can"]["default_id"], 16),
        )

    pipeline = AutoFuzzPipeline(cfg, can_iface=can_iface)
    summary = pipeline.run(max_seeds=max_seeds)

    _print_monitor_results(summary.get("last_result", {}))
    click.echo(click.style(f"Processed seeds : {summary.get('processed', 0)}", fg="cyan"))
    click.echo(click.style(f"Remaining queue : {summary.get('remaining_queue', 0)}", fg="cyan"))

    findings = summary.get("findings", [])
    if findings:
        click.echo(click.style("\n=== Findings ===\n", fg="magenta", bold=True))
        for item in findings:
            fusion = item.get("fusion", {})
            click.echo(
                f"seed={item.get('seed_id')} "
                f"arb_id={hex(int(item.get('arb_id')))} "
                f"verdict={fusion.get('verdict')} "
                f"repro_rate={fusion.get('repro_rate')} "
                f"children={item.get('child_ids', [])}"
            )
    else:
        click.echo("[i] No reproduced findings in this run.")

    if save_json is not None:
        save_json.parent.mkdir(parents=True, exist_ok=True)
        save_json.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        click.echo(click.style(f"[✓] Results saved → {save_json}", fg="green"))


if __name__ == "__main__":
    cli()