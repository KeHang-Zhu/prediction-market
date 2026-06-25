"""Multi-provider support: the provider-neutral tool-loop, the OpenAI-compatible
provider, the factory selection, and that the agentic loop is provider-agnostic.

These run fully offline — no model is called. The OpenAI path is exercised with a fake
client; the agentic loop with a fake provider injected via the get_provider factory.
"""

from __future__ import annotations

import pickle
from types import SimpleNamespace

import pytest

from market_sim.agents import scripted  # noqa: F401  (kept parallel to other tests)
from market_sim.agents.llm_agent import build_agentic_tool_specs
from market_sim.runner.config import AgentConfig, Capabilities, Config, MarketConfig


# --------------------------------------------------------------------------- tool specs

def test_tool_specs_are_openai_function_dicts():
    specs = build_agentic_tool_specs(None)
    for t in specs:
        assert t["type"] == "function"
        fn = t["function"]
        assert {"name", "description", "parameters"} <= set(fn)
        assert fn["parameters"]["type"] == "object"
    base = {t["function"]["name"] for t in specs}
    assert "place_order" in base and "commit_view" in base and "finish" in base
    # open-scenario tools are gated off by default
    assert {"transfer", "create_account", "create_market"} & base == set()


def test_tool_specs_capability_gating_matches_caps():
    full = {t["function"]["name"] for t in build_agentic_tool_specs(
        Capabilities(transfer=True, create_account=True, create_market=True))}
    assert {"transfer", "create_account", "create_market"} <= full
    # advanced_orders extends place_order's params; base form has only the five core fields
    def place_params(caps):
        for t in build_agentic_tool_specs(caps):
            if t["function"]["name"] == "place_order":
                return set(t["function"]["parameters"]["properties"])
        return set()
    assert place_params(Capabilities()) == {"market", "token", "side", "price", "qty"}
    assert {"order_type", "post_only", "expire_round"} <= place_params(
        Capabilities(advanced_orders=True))


def test_gemini_tool_conversion_preserves_names_and_required():
    pytest.importorskip("google.genai")
    from market_sim.llm.gemini import tool_specs_to_gemini
    tools = tool_specs_to_gemini(build_agentic_tool_specs(None))
    fds = tools[0].function_declarations
    names = {f.name for f in fds}
    assert {"get_markets", "place_order", "commit_view", "finish"} <= names
    po = next(f for f in fds if f.name == "place_order")
    assert set(po.parameters.required) == {"market", "token", "side", "price", "qty"}
    # nested array item schema survives the conversion
    cv = next(f for f in fds if f.name == "commit_view")
    assert cv.parameters.properties["beliefs"].items is not None


# --------------------------------------------------------------------------- factory

def test_get_provider_selection():
    from market_sim.llm import GeminiProvider, OpenAIProvider, get_provider
    assert isinstance(get_provider("gemini-3.5-flash"), GeminiProvider)
    assert isinstance(get_provider("deepseek-v4-pro"), OpenAIProvider)
    assert isinstance(get_provider("gpt-4o"), OpenAIProvider)
    assert isinstance(get_provider(None), GeminiProvider)             # default keeps Gemini
    # explicit provider overrides name inference
    assert isinstance(get_provider("my-local-llm", "vllm"), OpenAIProvider)
    ds = get_provider("deepseek-v4-pro")
    assert ds.base_url == "https://api.deepseek.com" and ds.api_key_env == "DEEPSEEK_API_KEY"
    with pytest.raises(ValueError):
        get_provider("x", "not-a-provider")


# --------------------------------------------------------------------------- OpenAI provider

class _FakeCompletions:
    def __init__(self, resp):
        self._resp, self.last_kwargs = resp, None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._resp


class _FakeClient:
    def __init__(self, resp):
        self.chat = SimpleNamespace(completions=_FakeCompletions(resp))


def _resp(content, tool_calls):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=tool_calls))])


def test_openai_tool_turn_parses_tool_calls():
    from market_sim.llm.openai_compat import OpenAIProvider
    tc = SimpleNamespace(id="call_x", function=SimpleNamespace(
        name="place_order", arguments='{"market":"COIN-A","price":45}'))
    p = OpenAIProvider(model="deepseek-v4-pro", pace=0)
    p._client = _FakeClient(_resp("hello", [tc]))
    tools = build_agentic_tool_specs(None)
    out = p.tool_turn([{"role": "user", "text": "hi"}], tools, system="sys")
    assert out["error"] is None
    assert out["function_calls"] == [
        {"id": "call_x", "name": "place_order", "args": {"market": "COIN-A", "price": 45}}]
    assert out["assistant"]["_native"] is None and out["text"] == "hello"
    kw = p._client.chat.completions.last_kwargs
    assert "max_tokens" in kw and "temperature" in kw      # non-reasoning model
    assert kw["tools"] is tools


def test_openai_tool_turn_no_tool_calls():
    from market_sim.llm.openai_compat import OpenAIProvider
    p = OpenAIProvider(model="gpt-4o", pace=0)
    p._client = _FakeClient(_resp("just text", None))
    out = p.tool_turn([{"role": "user", "text": "hi"}], build_agentic_tool_specs(None), system="s")
    assert out["function_calls"] == [] and out["text"] == "just text"
    assert out["assistant"]["tool_calls"] == []


def test_openai_message_translation_and_reasoning_flags():
    from market_sim.llm.openai_compat import OpenAIProvider
    p = OpenAIProvider(model="deepseek-reasoner", pace=0)
    msgs = p._to_messages("SYS", [
        {"role": "user", "text": "u"},
        {"role": "assistant", "text": "a",
         "tool_calls": [{"id": "c1", "name": "f", "args": {"x": 1}}], "_native": None},
        {"role": "tool", "tool_call_id": "c1", "name": "f", "result": {"ok": True}},
    ])
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[1] == {"role": "user", "content": "u"}
    assert msgs[2]["role"] == "assistant" and msgs[2]["tool_calls"][0]["id"] == "c1"
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "f"
    assert msgs[3]["role"] == "tool" and msgs[3]["tool_call_id"] == "c1"
    assert p._is_reasoning() is True            # deepseek-reasoner -> temperature dropped


def test_openai_empty_assistant_message_gets_placeholder_content():
    # a thinking-only turn (empty text, no tool_calls) must still serialize with content,
    # else DeepSeek/OpenAI 400 with "content or tool_calls must be set".
    from market_sim.llm.openai_compat import OpenAIProvider
    p = OpenAIProvider(model="deepseek-v4-flash", thinking=True, pace=0)
    msgs = p._to_messages("S", [{"role": "assistant", "text": "", "tool_calls": [], "_native": None}])
    assistant = msgs[1]
    assert assistant["content"] and "tool_calls" not in assistant


def test_openai_thinking_request_shape():
    from market_sim.llm.openai_compat import OpenAIProvider
    tc = SimpleNamespace(id="c1", function=SimpleNamespace(name="finish", arguments="{}"))
    p = OpenAIProvider(model="deepseek-v4-flash", thinking=True, reasoning_effort="low", pace=0)
    p._client = _FakeClient(_resp("", [tc]))
    p.tool_turn([{"role": "user", "text": "go"}], build_agentic_tool_specs(None), system="s")
    kw = p._client.chat.completions.last_kwargs
    assert kw["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kw["reasoning_effort"] == "low"
    assert "temperature" not in kw                # dropped in thinking mode


def test_openai_thinking_ignored_for_non_reasoning_model():
    # mixed run: thinking is ON globally, but a plain chat model (gpt-4o) must NOT receive
    # DeepSeek's extra_body / reasoning_effort (it would 400) and should keep its temperature.
    from market_sim.llm.openai_compat import OpenAIProvider
    tc = SimpleNamespace(id="c1", function=SimpleNamespace(name="finish", arguments="{}"))
    p = OpenAIProvider(model="gpt-4o", thinking=True, reasoning_effort="low", pace=0)
    p._client = _FakeClient(_resp("", [tc]))
    p.tool_turn([{"role": "user", "text": "go"}], build_agentic_tool_specs(None), system="s")
    kw = p._client.chat.completions.last_kwargs
    assert "extra_body" not in kw and "reasoning_effort" not in kw
    assert "temperature" in kw                    # non-reasoning model keeps temperature


def test_openai_transient_classification():
    from market_sim.llm.openai_compat import _openai_transient
    assert _openai_transient(Exception("429 Too Many Requests")) is True
    assert _openai_transient(Exception("Connection reset by peer")) is True
    assert _openai_transient(Exception("invalid api key")) is False


# --------------------------------------------------------------------------- builder passthrough

def test_builder_threads_models_and_provider():
    from market_sim.runner.builder import build_config
    cfg = build_config({"name": "mix", "llm_agentic": 3, "include_mm": False, "noise_count": 0,
                        "models": ["gemini-3.5-flash", "deepseek-v4-pro", "gpt-4o"]})
    llm = [a for a in cfg.agents if a.type == "llm_agentic"]
    assert [a.params["model"] for a in llm] == ["gemini-3.5-flash", "deepseek-v4-pro", "gpt-4o"]
    cfg2 = build_config({"name": "ds", "llm_agentic": 2, "include_mm": False, "noise_count": 0,
                         "model": "deepseek-v4-pro", "provider": "deepseek"})
    llm2 = [a for a in cfg2.agents if a.type == "llm_agentic"]
    assert all(a.params["provider"] == "deepseek" and a.params["model"] == "deepseek-v4-pro"
               for a in llm2)


def test_builder_llm_groups_expand_per_group():
    # grouped editor: each group -> `count` agents with its own model + thinking/effort.
    from market_sim.runner.builder import build_config
    cfg = build_config({"name": "grp", "include_mm": False, "noise_count": 0,
                        "llm_groups": [
                            {"model": "deepseek-v4-flash", "count": 2, "thinking": True, "reasoning_effort": "high"},
                            {"model": "gemini-3.5-flash", "count": 2, "thinking": False},
                            {"model": "gpt-4o", "count": 1, "thinking": False},
                        ]})
    llm = [a for a in cfg.agents if a.type == "llm_agentic"]
    assert len(llm) == 5
    assert [a.params["model"] for a in llm] == [
        "deepseek-v4-flash", "deepseek-v4-flash", "gemini-3.5-flash", "gemini-3.5-flash", "gpt-4o"]
    assert llm[0].params.get("thinking") is True and llm[0].params.get("reasoning_effort") == "high"
    assert "thinking" not in llm[2].params and "thinking" not in llm[4].params   # gemini/gpt groups off
    sigmas = [a.params["signal_sigma"] for a in llm]      # σ spread best->worst across ALL agents
    assert sigmas == sorted(sigmas) and sigmas[0] < sigmas[-1]
    # count:0 group contributes no agents
    cfg2 = build_config({"name": "g2", "include_mm": False, "noise_count": 0,
                         "llm_groups": [{"model": "deepseek-v4-flash", "count": 0, "thinking": True},
                                        {"model": "gemini-3.5-flash", "count": 1}]})
    assert [a.params["model"] for a in cfg2.agents if a.type == "llm_agentic"] == ["gemini-3.5-flash"]


# --------------------------------------------------------------------------- agentic loop (E2E, fake provider)

class _ScriptedProvider:
    """A fake provider that drives ToolLoopAgent through commit_view -> place_order ->
    finish using the neutral tool_turn contract — no model, no network."""

    def __init__(self):
        self.turn = 0
        self.seen_messages = []

    def tool_turn(self, messages, tools, *, system, temperature=None):
        self.seen_messages.append(list(messages))
        script = [
            ("commit_view", {"beliefs": [{"market": "COIN-A", "prob": 0.6}], "plan": "test"}),
            ("place_order", {"market": "COIN-A", "token": "YES", "side": "buy", "price": 45, "qty": 1}),
            ("finish", {"lessons": "learned"}),
        ]
        name, args = script[min(self.turn, len(script) - 1)]
        self.turn += 1
        fc = {"id": f"call_{self.turn}", "name": name, "args": args}
        assistant = {"role": "assistant", "text": "", "tool_calls": [fc], "_native": None}
        return {"assistant": assistant, "function_calls": [fc], "text": "",
                "error": None, "api_error": False, "retries": 0, "backoff_s": 0.0}


def test_agentic_loop_is_provider_neutral(monkeypatch):
    import market_sim.llm as providers_pkg
    from market_sim.runner.simulation import Runner
    from market_sim.runner.sinks import ListSink

    fake = _ScriptedProvider()
    monkeypatch.setattr(providers_pkg, "get_provider", lambda *a, **k: fake)

    cfg = Config(seed=1, rounds=1, max_actions_per_agent=16,
                 markets=[MarketConfig(id="COIN-A", true_prob=0.6, resolve_round=10 ** 9)],
                 agents=[
                     AgentConfig(id="mm", type="mm", initial_cash=500_000,
                                 params={"spread": 3, "size": 15}),
                     AgentConfig(id="llm1", type="llm_agentic", initial_cash=100_000),
                 ])
    r = Runner(cfg, ListSink())
    assert r.has_llm
    r.run(1)

    # the runner resets agent.last_call to None after emitting it as an llm_call event,
    # so read the event (proves the provider-neutral loop produced a real decision).
    agent = next(a for a in r.agents.values() if a.__class__.__name__ == "ToolLoopAgent")
    call = next(e.payload for e in r.sink.events
                if e.type == "llm_call" and e.agent_id == agent.agent_id)
    assert call["ok"] is True
    assert call["belief"] == {"COIN-A": 0.6}
    assert any("COIN-A" in a for a in call.get("actions", []))

    # the persistent conversation is provider-neutral dicts and survives a pickle round-trip
    assert agent.contents and all(isinstance(m, dict) for m in agent.contents)
    assert {m["role"] for m in agent.contents} <= {"user", "assistant", "tool"}
    # commit_view -> place_order -> finish appear in true order, each tool call answered
    roles = [m["role"] for m in agent.contents]
    assert roles == ["user", "assistant", "tool", "assistant", "tool", "assistant", "tool"]
    assert pickle.loads(pickle.dumps(agent.contents)) == agent.contents


def test_neutral_contents_pickle_roundtrip():
    contents = [
        {"role": "user", "text": "hello"},
        {"role": "assistant", "text": "", "tool_calls": [{"id": "c1", "name": "f", "args": {"a": 1}}],
         "_native": {"opaque": "stand-in for a native turn object"}},
        {"role": "tool", "tool_call_id": "c1", "name": "f", "result": {"ok": True}},
    ]
    assert pickle.loads(pickle.dumps(contents)) == contents
