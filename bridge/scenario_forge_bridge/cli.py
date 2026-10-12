"""`scenario-forge-bridge` (alias `sfbridge`) command line."""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

import typer

from scenario_forge_bridge import __version__, api, credentials, machine, runtime
from scenario_forge_bridge import config as config_store
from scenario_forge_bridge.carla_catalog import CarlaUnavailable
from scenario_forge_bridge.carla_probe import probe_carla
from scenario_forge_bridge.channel import ChannelRejected
from scenario_forge_bridge.channel import run as run_channel
from scenario_forge_bridge.config import Connection
from scenario_forge_bridge.runner import runner_problems, version_mismatch

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
    problems = runner_problems(cfg)
    state = "sẵn sàng" if not problems else "chưa sẵn sàng"
    typer.echo(f"Runner       : {state} · CARLA {cfg.carla_version or '—'} · python={cfg.runner_python or '—'}")
    for problem in problems:
        typer.echo(f"  - {problem}")
    if probe.reachable and not problems:
        mismatch = version_mismatch(cfg)
        if mismatch:
            typer.secho(f"  ! {mismatch}", fg=typer.colors.YELLOW)
    typer.echo(f"Camera       : {cfg.camera}")
    if not cfg.connections:
        typer.echo("Project      : chưa ghép project nào")
        return
    typer.echo("Project      :")
    for item in cfg.connections:
        typer.echo(f"  - {item.project_name} (#{item.project_id}) · {item.connection_uid} · ghép lúc {item.paired_at}")


AUTO = "auto"


class DetectFailed(Exception):
    """Auto-detect could not settle on a CARLA version: the menu is shown again."""


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _choose_version() -> str:
    import questionary

    typer.echo("Scenario Forge Bridge · Chọn phiên bản CARLA server trên máy này")
    typer.echo("(↑/↓ để chọn, Enter để xác nhận)")
    choices = [questionary.Choice(f"CARLA {version}", value=version) for version in runtime.RUNNERS]
    choices.append(questionary.Choice("Tự động phát hiện  (CARLA phải đang chạy)", value=AUTO))
    answer = questionary.select("", choices=choices, qmark="", instruction=" ", pointer="❯").ask()
    if answer is None:  # Ctrl+C
        raise typer.Exit(1)
    return answer


def _auto_detect(cfg, roots: list[Path]) -> tuple[str, Path | None]:
    probe = probe_carla(cfg.carla_host, cfg.carla_port)
    if not probe.reachable:
        raise DetectFailed(f"CARLA chưa chạy ở {probe.host}:{probe.port}. Mở CARLA rồi chọn lại, hoặc chọn phiên bản trong danh sách.")
    running = runtime.running_carla_root()
    if running and runtime.carla_version_of(running):
        return runtime.carla_version_of(running), running
    # A runner set up earlier can ask the server itself.
    for known in runtime.RUNNERS:
        python = runtime.venv_python(runtime.runtime_dir(known) / "venv")
        if python.is_file():
            actual = runtime.server_version(str(python), cfg.carla_host, cfg.carla_port)
            if actual:
                return actual, next((root for root in roots if runtime.carla_version_of(root) == actual), None)
    versions = {runtime.carla_version_of(root) for root in roots} - {None}
    if len(versions) == 1:
        version = versions.pop()
        return version, next(root for root in roots if runtime.carla_version_of(root) == version)
    raise DetectFailed("Không tự xác định được phiên bản CARLA đang chạy. Hãy chọn phiên bản trong danh sách.")


def _pick_root(version: str, roots: list[Path], interactive: bool) -> Path | None:
    """The CARLA folder of `version` (its PythonAPI/carla/agents is needed by ScenarioRunner)."""
    matching = [root for root in roots if runtime.carla_version_of(root) == version]
    if matching:
        return matching[0]
    for root in roots:
        typer.secho(f"Thư mục {root} là CARLA {runtime.carla_version_of(root) or '?'}, không phải {version}.", fg=typer.colors.YELLOW)
    if not interactive:
        return None
    import questionary

    while True:
        answer = questionary.path(f"Thư mục cài CARLA {version} (Enter để bỏ qua):", only_directories=True, qmark="").ask()
        if not answer:
            return None
        root = Path(answer).expanduser().resolve()
        if runtime.is_carla_root(root):
            return root
        typer.secho(f"{root} không có PythonAPI/carla/agents: chưa phải thư mục CARLA.", fg=typer.colors.RED)


@app.command("setup-runner")
def setup_runner(
    carla: str = typer.Option(None, "--carla", help=f"Phiên bản CARLA server: {', '.join(runtime.RUNNERS)} hoặc auto. Bỏ trống để chọn trong menu."),
    carla_root: str = typer.Option(None, "--carla-root", help="Thư mục cài CARLA (mặc định: tự tìm)."),
) -> None:
    """Chuẩn bị môi trường chạy test case trên CARLA (Python, ScenarioRunner) cho đúng phiên bản CARLA của máy này."""
    cfg = config_store.load()
    interactive = _interactive()
    if carla is None and not interactive:
        fail(f"Không có màn hình để chọn: thêm --carla <{'|'.join(runtime.RUNNERS)}|auto>.")
    if carla and carla != AUTO and carla not in runtime.RUNNERS:
        fail(f"CARLA {carla} chưa được hỗ trợ (hỗ trợ: {', '.join(runtime.RUNNERS)}).")
    roots = runtime.find_carla_roots(carla_root)
    while True:
        choice = carla or _choose_version()
        try:
            if choice == AUTO:
                version, root = _auto_detect(cfg, roots)
                if version not in runtime.RUNNERS:
                    raise DetectFailed(f"Máy đang chạy CARLA {version}: phiên bản này chưa được hỗ trợ.")
                ok(f"Phát hiện CARLA {version}" + (f" ở {root}" if root else ""))
            else:
                version, root = choice, None
            break
        except DetectFailed as exc:
            typer.secho(str(exc), fg=typer.colors.YELLOW)
            if carla or not interactive:
                raise typer.Exit(2) from exc
    root = root or _pick_root(version, roots, interactive)
    if root is None:
        typer.secho("Chưa có thư mục CARLA: cài xong nhưng chưa chạy được test. Chạy lại với --carla-root <thư mục CARLA>.",
                    fg=typer.colors.YELLOW)
    typer.echo(f"Chuẩn bị môi trường cho CARLA {version} (một lần, vài phút)…")
    try:
        cfg = runtime.install(version, root, log)
    except runtime.SetupFailed as exc:
        fail(str(exc))
    if root is None:
        typer.secho(f"Đã cài môi trường CARLA {version}, nhưng còn thiếu thư mục CARLA nên chưa chạy được test. "
                    f"Chạy lại: scenario-forge-bridge setup-runner --carla {version} --carla-root <thư mục CARLA>", fg=typer.colors.YELLOW)
    else:
        ok(f"Đã sẵn sàng chạy test case trên CARLA {version}.")
    if not credentials.load_token():
        typer.echo("Tiếp theo: lấy mã 6 số ở trang Start up rồi chạy  scenario-forge-bridge pair <mã>")
    else:
        typer.echo("Tiếp theo: scenario-forge-bridge run")


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
        for result in runtime.collect(cfg, maps=wanted, load_opt=load_opt, load_timeout=load_timeout,
                              on_loading=lambda i, n, name: log(f"Đang mở map {i}/{n}: {name} (map lớn có thể mất vài phút)")):
            if result.error:
                failed += 1
                typer.secho(f"  ✗ {result.map_name}: {result.error}", fg=typer.colors.RED)
                continue
            for target in targets:
                try:
                    stored = api.upload_catalog(
                        cfg.server, token, target.connection_uid, result.catalog,
                        on_retry=lambda n, wait, why, name=result.map_name: log(f"  ↻ Gửi lại {name} (lần {n}) sau {wait} giây: {why}"),
                    )
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
    runner_python: str = typer.Option(None, "--runner-python", help="Python đã cài carla + thư viện ScenarioRunner, ví dụ ~/sr-venv/bin/python."),
    runner_root: str = typer.Option(None, "--runner-root", help="Thư mục chứa scenario_runner.py, ví dụ ~/scenario_runner."),
    carla_root: str = typer.Option(None, "--carla-root", help="Thư mục cài CARLA (có PythonAPI/carla/agents)."),
    camera: str = typer.Option(None, "--camera", help="follow: camera cửa sổ CARLA bám xe ego khi chạy test; off: không đụng tới camera."),
) -> None:
    """Xem hoặc đổi cấu hình (máy chủ, địa chỉ CARLA, tên máy, Simulator Runner)."""
    cfg = config_store.load()
    if camera and camera not in ("follow", "off"):
        fail("--camera chỉ nhận follow hoặc off.")
    paths = {key: os.path.abspath(os.path.expanduser(value)) if value else value
             for key, value in (("runner_python", runner_python), ("runner_root", runner_root), ("carla_root", carla_root))}
    changed = False
    for field, value in (("server", server and server.rstrip("/")), ("carla_host", carla_host), ("carla_port", carla_port), ("name", name),
                         *paths.items(), ("camera", camera)):
        if value:
            setattr(cfg, field, value)
            changed = True
    if changed:
        config_store.save(cfg)
        log("Đã lưu cấu hình.")
    typer.echo(f"server={cfg.server}\ncarla={cfg.carla_host}:{cfg.carla_port}\nname={cfg.name or machine.hostname()}")
    typer.echo(f"runner_python={cfg.runner_python}\nrunner_root={cfg.runner_root}\ncarla_root={cfg.carla_root}\ncamera={cfg.camera}")
    for problem in runner_problems(cfg):
        typer.secho(f"  ! {problem}", fg=typer.colors.YELLOW)


def main() -> None:
    app()
