import asyncio

import typer

from .config import Settings
from .dashboard import run_dashboard
from .engine import Engine
from .market_data import GammaClient
from .store import AuditStore

app = typer.Typer(no_args_is_help=True)


@app.command()
def scan(limit: int = 20) -> None:
    """Show liquid active binary markets without trading."""
    async def run() -> None:
        settings = Settings()
        client = GammaClient(settings)
        try:
            for market in (await client.active_binary_markets(limit=limit))[:limit]:
                typer.echo(
                    f"{market.yes_ask:.3f}/{market.no_ask:.3f} "
                    f"liq={market.liquidity:.0f} {market.question}"
                )
        finally:
            await client.close()
    asyncio.run(run())


@app.command()
def run(once: bool = typer.Option(True, help="Run one paper cycle.")) -> None:
    """Run the risk-gated paper engine."""
    async def execute() -> None:
        engine = Engine(Settings())
        try:
            result = await engine.cycle()
            typer.echo(result)
        finally:
            await engine.close()
    if not once:
        raise typer.BadParameter("Continuous scheduler arrives after one-cycle validation.")
    asyncio.run(execute())


@app.command()
def stream(
    minutes: int = typer.Option(
        0, min=0, help="Run real-time paper mode for N minutes; 0 runs until stopped."
    ),
) -> None:
    """Run event-driven paper trading from Polymarket's public WebSocket."""

    async def execute() -> None:
        engine = Engine(Settings())
        try:
            result = await engine.stream(duration_seconds=minutes * 60)
            typer.echo(result)
        finally:
            await engine.close()

    try:
        asyncio.run(execute())
    except KeyboardInterrupt:
        typer.echo("Real-time paper engine stopped.")


@app.command()
def dashboard(
    port: int = typer.Option(8765, min=1024, max=65535),
    open_browser: bool = typer.Option(True, help="Open the local dashboard in a browser."),
) -> None:
    """Start the real-time paper engine and local browser dashboard."""
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"Polybot dashboard: {url}")
    try:
        asyncio.run(
            run_dashboard(
                Settings(),
                port=port,
                open_browser=open_browser,
            )
        )
    except KeyboardInterrupt:
        typer.echo("Dashboard and real-time paper engine stopped.")


@app.command()
def status() -> None:
    """Show persistent paper observation history."""
    settings = Settings()
    store = AuditStore(settings.db_path)
    try:
        summary = store.summary()
    finally:
        store.close()
    typer.echo(f"Recorded events: {summary['total_events']}")
    typer.echo(f"Counts: {summary['counts']}")
    typer.echo(f"Latest cycle: {summary['latest_cycle_at']}")
    typer.echo(summary["latest_cycle"] or "No cycle recorded.")
    typer.echo(f"Paper account: {summary['paper']}")
