"""`scenario-forge-bridge` (alias `sfbridge`) command line."""
from __future__ import annotations

import asyncio
from datetime import datetime

import typer

from scenario_forge_bridge import __version__, api, credentials, machine
from scenario_forge_bridge import config as config_store
from scenario_forge_bridge.carla_catalog import CarlaUnavailable, collect
from scenario_forge_bridge.carla_probe import probe_carla
from scenario_forge_bridge.channel import ChannelRejected
from scenario_forge_bridge.channel import run as run_channel
from scenario_forge_bridge.config import Connection

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Scenario Forge Bridge: kết nối máy chạy CARLA với Project trên Scenario Forge.",
)


def log(message: str) -> None:
    typer.echo(f"[{datetime.now():%H:%M:%S}] {message}")


def ok(message: str) -> None:
    typer.secho(message, fg=typer.colors.GREEN, bold=True)


def fail(message: str, code: int = 1) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"scenario-forge-bridge {__version__}")
        raise typer.Exit()


@app.callback()
def root(version: bool = typer.Option(False, "--version", callback=version_callback, is_eager=True, help="In phiên bản rồi thoát.")) -> None:
    """Scenario Forge Bridge."""


def _run_forever(cfg, token: str) -> None:
    log(f"Đang mở kênh tới {cfg.ws_url} (Ctrl+C để dừng)")
    try:
        asyncio.run(run_channel(cfg, token, log))
    except ChannelRejected as exc:
        fail(str(exc))
    except KeyboardInterrupt:
        log("Đã dừng Bridge.")


@app.command()
def pair(
    code: str = typer.Argument(..., help="Mã 6 số hiển thị ở trang Start up của Project."),
    server: str = typer.Option(None, "--server", "-s", help="Địa chỉ API, ví dụ https://forge.example.com/api/v1"),
    name: str = typer.Option(None, "--name", "-n", help="Tên hiển thị của máy này (mặc định: hostname)."),
    carla_host: str = typer.Option(None, "--carla-host", help="Máy chạy CARLA (mặc định 127.0.0.1)."),
    carla_port: int = typer.Option(None, "--carla-port", help="Cổng RPC của CARLA (mặc định 2000)."),
    run: bool = typer.Option(True, "--run/--no-run", help="Giữ kết nối sau khi ghép (mặc định có)."),
) -> None:
    """Ghép máy này với một Project bằng mã OTP 6 số."""
    code = code.strip().replace(" ", "")
    if not (len(code) == 6 and code.isdigit()):
        fail("Mã ghép nối phải gồm đúng 6 chữ số.")
    cfg = config_store.load()
    if server:
        cfg.server = server.rstrip("/")
    if name:
        cfg.name = name
    if carla_host:
        cfg.carla_host = carla_host
    if carla_port:
        cfg.carla_port = carla_port

    probe = probe_carla(cfg.carla_host, cfg.carla_port)
    if probe.reachable:
        log(f"CARLA {probe.host}:{probe.port} đã chạy.")
    else:
        typer.secho(f"Cảnh báo: chưa thấy CARLA ở {probe.host}:{probe.port} ({probe.error}). Vẫn ghép nối được, hãy mở CARLA sau.", fg=typer.colors.YELLOW)

    token = credentials.load_token()
    try:
        result = api.pair(
            cfg.server, code, token=token, name=cfg.name or machine.hostname(),
            hostname=machine.hostname(), os=machine.os_label(), carla=probe,
        )
    except api.BridgeApiError as exc:
        fail(f"Ghép nối thất bại: {exc}")

    if result.get("device_token"):
        where = credentials.save_token(result["device_token"])
        token = result["device_token"]
        log(f"Đã lưu device token ({where}).")
    connection = result["connection"]
    cfg.bridge_uid = result["bridge_uid"]
    cfg.upsert_connection(Connection(**connection))
    config_store.save(cfg)

    ok("Kết nối thành công!")
    typer.echo(f"  Project       : {connection['project_name']} (#{connection['project_id']})")
    typer.echo(f"  Mã kết nối    : {connection['connection_uid']}")
    typer.echo(f"  Bridge        : {cfg.bridge_uid}")
    if not run:
        typer.echo("Chạy `scenario-forge-bridge run` để giữ Bridge trực tuyến.")
        return
    _run_forever(cfg, token)


@app.command()
def run() -> None:
    """Giữ Bridge trực tuyến: kết nối máy chủ, gửi heartbeat và trạng thái CARLA, tự kết nối lại khi mất mạng."""
    cfg = config_store.load()
    token = credentials.load_token()
    if not token:
        fail("Máy này chưa được ghép. Chạy `scenario-forge-bridge pair <mã 6 số>` trước.")
    _run_forever(cfg, token)


@app.command()
def status() -> None:
    """Thông tin Bridge, các Project đã ghép và trạng thái CARLA."""
    cfg = config_store.load()
    token = credentials.load_token()
    typer.echo(f"Phiên bản    : {__version__}")
    typer.echo(f"Máy chủ      : {cfg.server}")
    typer.echo(f"Bridge       : {cfg.bridge_uid or '—'} ({'đã có token' if token else 'chưa ghép'})")
    typer.echo(f"Máy          : {machine.hostname()} · {machine.os_label()}")
    typer.echo(f"Cấu hình     : {config_store.path()}")
    probe = probe_carla(cfg.carla_host, cfg.carla_port)
    carla = "đã chạy" if probe.reachable else f"chưa chạy ({probe.error})"
    typer.echo(f"CARLA        : {probe.host}:{probe.port} {carla}")
    if not cfg.connections:
        typer.echo("Project      : chưa ghép project nào")
        return
    typer.echo("Project      :")
    for item in cfg.connections:
        typer.echo(f"  - {item.project_name} (#{item.project_id}) · {item.connection_uid} · ghép lúc {item.paired_at}")


@app.command("check-carla")
def check_carla(
    host: str = typer.Option(None, "--host", help="Mặc định lấy từ cấu hình (127.0.0.1)."),
    port: int = typer.Option(None, "--port", help="Mặc định lấy từ cấu hình (2000)."),
) -> None:
    """Kiểm tra cổng CARLA (mặc định 2000) đã mở chưa. Mã thoát 0 = đã chạy, 2 = chưa chạy."""
    cfg = config_store.load()
    probe = probe_carla(host or cfg.carla_host, port or cfg.carla_port)
    if probe.reachable:
        ok(f"CARLA đang chạy ở {probe.host}:{probe.port}.")
        return
    fail(f"Không thấy CARLA ở {probe.host}:{probe.port}: {probe.error}. Hãy mở CarlaUE4 (Windows) hoặc CarlaUE4.sh (Ubuntu).", code=2)


@app.command()
def sync(
    maps: str = typer.Option(None, "--maps", help="Chỉ đồng bộ các map này, cách nhau dấu phẩy (mặc định: tất cả)."),
    connection_uid: str = typer.Option(None, "--connection", "-c", help="Chỉ gửi cho Project có mã kết nối này (mặc định: mọi Project đã ghép)."),
    load_opt: bool = typer.Option(False, "--load-opt", help="Mở riêng cả bản _Opt (mặc định dùng chung dữ liệu đường với bản thường)."),
    load_timeout: int = typer.Option(300, "--load-timeout", help="Số giây tối đa chờ CARLA mở một map."),
) -> None:
    """Đọc dữ liệu mọi map của CARLA (spawn point, làn đường, phương tiện, thời tiết) rồi gửi lên Project.

    Thường không cần gõ lệnh này: bấm "Đồng bộ dữ liệu CARLA" trên trang Start up khi Bridge đang chạy.
    """
    cfg = config_store.load()
    token = credentials.load_token()
    if not token:
        fail("Máy này chưa được ghép. Chạy `scenario-forge-bridge pair <mã 6 số>` trước.")
    targets = [item for item in cfg.connections if not connection_uid or item.connection_uid == connection_uid]
    if not targets:
        fail("Không có Project nào để gửi dữ liệu (xem `status`).")
    wanted = [item.strip() for item in maps.split(",") if item.strip()] if maps else None
    typer.secho("Lưu ý: Bridge sẽ lần lượt mở từng map trong CARLA rồi mở lại map hiện tại khi xong.", fg=typer.colors.YELLOW)
    synced = failed = 0
    try:
        for result in collect(cfg.carla_host, cfg.carla_port, maps=wanted, load_opt=load_opt, load_timeout=load_timeout,
                              on_loading=lambda i, n, name: log(f"Đang mở map {i}/{n}: {name} (map lớn có thể mất vài phút)")):
            if result.error:
                failed += 1
                typer.secho(f"  ✗ {result.map_name}: {result.error}", fg=typer.colors.RED)
                continue
            for target in targets:
                try:
                    stored = api.upload_catalog(cfg.server, token, target.connection_uid, result.catalog)
                except api.BridgeApiError as exc:
                    failed += 1
                    typer.secho(f"  ✗ {result.map_name} → {target.project_name}: {exc}", fg=typer.colors.RED)
                    continue
                synced += 1
                state = "mới" if stored.get("created") else "không đổi"
                log(f"  ✓ {result.map_name} → {target.project_name} (snapshot #{stored.get('snapshot_id')}, {state})")
    except CarlaUnavailable as exc:
        fail(f"{exc}\nĐã gửi {synced} map trước khi dừng.")
    if failed:
        fail(f"Xong với {failed} lỗi, {synced} map đã gửi.")
    ok(f"Đã đồng bộ {synced} map.")


@app.command()
def unpair(
    connection_uid: str = typer.Argument(None, help="Mã kết nối cần gỡ (xem bằng `status`)."),
    all_: bool = typer.Option(False, "--all", help="Gỡ mọi project và xóa device token khỏi máy."),
) -> None:
    """Gỡ máy này khỏi một Project (hoặc tất cả với --all)."""
    cfg = config_store.load()
    token = credentials.load_token()
    targets = [item.connection_uid for item in cfg.connections] if all_ else [connection_uid] if connection_uid else []
    if not targets:
        fail("Hãy chỉ định mã kết nối hoặc dùng --all.")
    for uid in targets:
        if token:
            try:
                api.unpair(cfg.server, token, uid)
            except api.BridgeApiError as exc:
                typer.secho(f"Không gỡ được {uid} trên máy chủ: {exc}", fg=typer.colors.YELLOW)
        cfg.remove_connection(uid)
        log(f"Đã gỡ {uid}.")
    if all_:
        credentials.delete_token()
        cfg.bridge_uid = None
        log("Đã xóa device token khỏi máy.")
    config_store.save(cfg)


@app.command("config")
def set_config(
    server: str = typer.Option(None, "--server", "-s", help="Địa chỉ API của Scenario Forge."),
    carla_host: str = typer.Option(None, "--carla-host"),
    carla_port: int = typer.Option(None, "--carla-port"),
    name: str = typer.Option(None, "--name", "-n"),
) -> None:
    """Xem hoặc đổi cấu hình (máy chủ, địa chỉ CARLA, tên máy)."""
    cfg = config_store.load()
    changed = False
    for field, value in (("server", server and server.rstrip("/")), ("carla_host", carla_host), ("carla_port", carla_port), ("name", name)):
        if value:
            setattr(cfg, field, value)
            changed = True
    if changed:
        config_store.save(cfg)
        log("Đã lưu cấu hình.")
    typer.echo(f"server={cfg.server}\ncarla={cfg.carla_host}:{cfg.carla_port}\nname={cfg.name or machine.hostname()}")


def main() -> None:
    app()
