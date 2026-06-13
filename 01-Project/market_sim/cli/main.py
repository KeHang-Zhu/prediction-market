"""typer CLI. Each command parses flags, calls the shared ``dispatch``, and renders
the result. State persists between invocations via the Session pickle.
"""

from __future__ import annotations

import typer

from market_sim.commands.handlers import dispatch
from market_sim.commands.session import Session

app = typer.Typer(add_completion=False, help="Generative Market Simulation — V0 engine CLI")
order_app = typer.Typer(help="manual (human) orders")
app.add_typer(order_app, name="order")


def _render(result, save_session: Session | None = None) -> None:
    if result.ok:
        typer.echo(result.text)
        if save_session is not None:
            save_session.save()
    else:
        typer.secho(f"error: {result.error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


def _load_or_die() -> Session:
    s = Session()
    if not s.load():
        typer.secho("no active run — start one with `market init --config <file.yaml>`",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    return s


@app.command()
def init(config: str = typer.Option(..., "--config", "-c", help="experiment YAML")):
    """Initialize a run from a config file."""
    s = Session()
    _render(dispatch(s, "init", {"config": config}), save_session=s)


@app.command()
def run(rounds: int = typer.Option(None, "--rounds", "-n", help="rounds to advance")):
    """Advance the simulation by N rounds (default: config horizon)."""
    s = _load_or_die()
    _render(dispatch(s, "run", {"rounds": rounds}), save_session=s)


@app.command()
def step():
    """Advance one round (debugging)."""
    s = _load_or_die()
    _render(dispatch(s, "step", {}), save_session=s)


@app.command()
def book(market: str = typer.Argument(..., help="market id")):
    """Show an order book."""
    s = _load_or_die()
    _render(dispatch(s, "book", {"market": market}))


@app.command()
def portfolio(agent: str = typer.Argument(..., help="agent id")):
    """Show an account (available / locked / positions / open orders)."""
    s = _load_or_die()
    _render(dispatch(s, "portfolio", {"agent": agent}))


@app.command()
def tape(market: str = typer.Argument(...), last: int = typer.Option(20, "--last", "-l")):
    """Show recent trades."""
    s = _load_or_die()
    _render(dispatch(s, "tape", {"market": market, "last": last}))


@app.command()
def status():
    """Show the current run state."""
    s = _load_or_die()
    _render(dispatch(s, "status", {}))


@order_app.command("place")
def order_place(
    agent: str = typer.Option(..., "--agent"),
    market: str = typer.Option(..., "--market"),
    token: str = typer.Option(..., "--token", help="YES or NO"),
    side: str = typer.Option(..., "--side", help="buy or sell"),
    price: int = typer.Option(..., "--price"),
    qty: int = typer.Option(..., "--qty"),
):
    """Place a manual order as an agent (executes immediately against the book)."""
    s = _load_or_die()
    _render(dispatch(s, "order_place", {"agent": agent, "market": market, "token": token,
                                        "side": side, "price": price, "qty": qty}), save_session=s)


@app.command()
def cancel(agent: str = typer.Option(..., "--agent"), order_id: int = typer.Option(..., "--order-id")):
    """Cancel an order."""
    s = _load_or_die()
    _render(dispatch(s, "cancel", {"agent": agent, "order_id": order_id}), save_session=s)


@app.command()
def replay(log: str = typer.Option(..., "--log", help="JSONL run log to verify")):
    """Verify byte-exact replay of a run log."""
    _render(dispatch(Session(), "replay", {"log": log}))


@app.command()
def plot(log: str = typer.Option(None, "--log"), out: str = typer.Option(None, "--out")):
    """Render a price-trajectory and volume figure from a run log."""
    s = Session()
    s.load()  # best effort, for the default log path
    _render(dispatch(s, "plot", {"log": log, "out": out}))


if __name__ == "__main__":
    app()
