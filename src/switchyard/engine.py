"""Warm, CPU-only Laya backend. Call serially through DecisionService."""

import contextlib
import sys
import time

from .budget import check_budget
from .config import Settings
from .contracts import DecisionRequest, validate_answers
from .errors import DecisionError
from .models import REVISION, model_path, offline_environment, verify_model


class LayaEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.agents = {}
        self.router = None

    def load(self):
        for name in self.settings.models:
            verify_model(self.settings, name)
        offline_environment()
        try:
            import laya
            import torch
            from laya import Router

            torch.set_num_threads(self.settings.threads)
            self.router = Router(max_loaded=len(self.settings.models), device="cpu")
            with contextlib.redirect_stdout(sys.stderr):
                for name in self.settings.models:
                    agent = laya.load(str(model_path(self.settings, name)), device="cpu")
                    self.agents[name] = agent
                    self.router.attach(name, agent)
        except ImportError as error:
            raise DecisionError(
                "dependency_missing", "Install switchyard[inference]"
            ) from error
        except Exception as error:
            raise DecisionError(
                "model_load_failed", "Model could not be loaded; run switchyard doctor"
            ) from error

    def capabilities(self):
        return {
            "protocol_version": 1,
            "ready": bool(self.agents),
            "device": "cpu",
            "models": {
                name: {
                    "revision": REVISION,
                    "max_len": agent.cfg.get("max_len", 512),
                    "head_max_len": agent.cfg.get("head_max_len", 192),
                }
                for name, agent in self.agents.items()
            },
            "question_types": ["choice", "score", "noul"],
            "automatic_omission": False,
            "max_questions": 32,
            "max_choices": 20,
            "threads": self.settings.threads,
        }

    def decide(self, request: DecisionRequest) -> dict:
        started = time.monotonic()
        questions = {key: q.model_dump(exclude_none=True) for key, q in request.questions.items()}
        if self.router is None:
            raise DecisionError("model_unavailable", "Runtime models have not been loaded")
        if request.model == "auto":
            routing = dict(self.router.route(request.state, questions))
            name = routing["model"]
        else:
            name = request.model
            routing = {"model": name, "reason": "Caller-selected checkpoint"}
        if name not in self.agents:
            raise DecisionError("model_unavailable", "Selected checkpoint is not loaded")
        agent = self.agents[name]
        budgets = {
            key: check_budget(
                agent.tok,
                request.state,
                question,
                max_len=agent.cfg.get("max_len", 512),
                head_max_len=agent.cfg.get("head_max_len", 192),
            )
            for key, question in request.questions.items()
        }
        try:
            with contextlib.redirect_stdout(sys.stderr):
                result = agent.predict(request.state, questions)
            answers = validate_answers(request.questions, result["answers"])
        except DecisionError:
            raise
        except Exception as error:
            raise DecisionError("inference_failed", "Model inference failed") from error
        return {
            "answers": answers,
            "model": name,
            "revision": REVISION,
            "routing": routing,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
            "usage": result.get("usage", {}),
            "budgets": budgets,
            "calibration": "Not calibrated for your application; evaluate before automating",
        }
