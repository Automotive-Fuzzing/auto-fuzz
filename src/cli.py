# src/cli.py
import json
import click
from pipeline import AutoFuzzPipeline


@click.group()
def cli():
    """Auto-Fuzz CLI — Seed registration, listing, and fuzzing."""
    pass

# 시드 등록
@cli.command()
@click.option("--dbc", required=True, type=click.Path(exists=True))
@click.option("--db", default="seeds.db")
def register(dbc, db):
    click.echo(f"[+] Parsing DBC: {dbc}")
    count = AutoFuzzPipeline.register_seeds(dbc, db)
    click.echo(f"[✓] {count} seeds registered into '{db}'")


# 시드 목록 출력
@cli.command()
@click.option("--db", default="seeds.db")
def list(db):
    seeds = AutoFuzzPipeline.list_seeds(db)
    if not seeds:
        click.echo("[!] No seeds found.")
        return

    click.echo(f"[+] {len(seeds)} seeds loaded:")
    click.echo("-" * 60)
    for s in seeds:
        click.echo(f"[{s.id:03}] {s.signal_name:<25} | msg_id={hex(s.message_id)} | priority={s.priority}")
    click.echo("-" * 60)


# Monitor 결과 출력
def print_monitor_results(results: dict):
    click.echo(click.style("\n=== AutoFuzz Monitor Results ===\n", fg="cyan", bold=True))

    for name, v in results.items():
        monitor = name.upper()

        # 상태 색상 + 아이콘
        if v["completed"]:
            status_icon = click.style("✓", fg="green")
            status_text = click.style("completed", fg="green")
        else:
            status_icon = click.style("✗", fg="red")
            status_text = click.style("timeout", fg="red")

        # 점수 색상
        score = click.style(f"{v['score']:.3f}", fg="cyan")

        click.echo(
            f"{status_icon} {click.style(monitor, bold=True):<10} "
            f"Score: {score}  ({status_text})"
        )
        
    click.echo()


# 퍼저 실행
@cli.command()
@click.option("--db", default="seeds.db")
@click.option("--channel", default="vcan0")
@click.option("--can", is_flag=True)
@click.option("--can-id", default="0x6A6")
@click.option("--save-json", is_flag=True, help="Save monitor results to fuzz_results.json")
def run(db, channel, can, can_id, save_json):
    """Run Auto-Fuzz pipeline."""

    # CAN ID 파싱
    try:
        can_id_int = int(can_id, 16)
        click.echo(f"[✓] Using CAN ID {hex(can_id_int)}")
    except ValueError:
        click.echo("[!] Invalid CAN ID format.")
        return

    # 인터페이스 준비
    can_iface = None
    if can:
        from interface.can_interface import CANInterface
        can_iface = CANInterface(channel=channel, can_id=can_id_int)
        click.echo(f"[+] CAN Interface initialized on {channel}")

    # 파이프라인 실행
    pipeline = AutoFuzzPipeline(db, can_iface=can_iface)
    results = pipeline.run()

    # 모니터 결과 출력
    print_monitor_results(results)

    # JSON 저장 옵션
    if save_json:
        with open("fuzz_results.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=4)
        click.echo(click.style("[✓] Results saved → fuzz_results.json", fg="green"))


if __name__ == "__main__":
    cli()