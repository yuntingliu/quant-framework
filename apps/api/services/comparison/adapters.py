from __future__ import annotations





import json


import os


from pathlib import Path


from typing import Any, Dict, Iterator, List





from .core import Adapter, BudgetGuard, Config, Limits, Task








class ConexusAdapter(Adapter):


    def __init__(self):


        self.financial_entry_points = []


        self.events = []





    def setup(self, task: Task, limits: Limits, initial_state: Dict[str, Any], use_doubles: bool) -> None:


        pass





    def run(self, task: Task, limits: Limits, budget_guard: BudgetGuard) -> Iterator[Dict[str, Any]]:


        # Use existing Conexus flow pattern; with doubles via scripted model


        ev = {"type": "conexus.start", "task_id": task.task_id}


        self.events.append(ev)


        yield ev


        # Simulate financial entry points (would come from actual calls)


        self.financial_entry_points = [{"endpoint": "/api/backtests/jobs", "method": "POST"}, {"endpoint": "/api/strategy/projects", "method": "GET"}]


        yield {"type": "conexus.done", "task_id": task.task_id}





    def stop(self) -> None:


        pass





    def get_capabilities(self) -> Dict[str, Any]:


        return {"has_financial_tools": True}





    def get_financial_entry_points(self) -> List[Dict[str, Any]]:


        return self.financial_entry_points








class OpenCodeAdapter(Adapter):


    def __init__(self, config: Config):


        self.config = config  # A or B


        self.financial_entry_points = []


        self.tool_files_loaded = False


        self.loaded_tools = {}





    def _load_tool_jsons(self):


        base = Path(__file__).resolve().parents[4] / "integrations" / "conexus" / "alphalab-research-agent" / "tools"


        files = ["Project-Files.tool.json", "Project-Run.tool.json"]


        for fn in files:


            p = base / fn


            if not p.exists():


                raise FileNotFoundError(f"Cannot load required tool file: {p}")


            with open(p, "r", encoding="utf-8") as f:


                self.loaded_tools[fn] = json.load(f)


        self.tool_files_loaded = True





    def _extract_endpoints_from_tools(self) -> List[Dict[str, Any]]:


        # Extract from tool code heuristics (same as tools do)


        eps = []


        # common endpoints used by tools


        eps.append({"endpoint": "/api/strategy/projects", "method": "GET"})


        eps.append({"endpoint": "/api/backtests/jobs", "method": "POST"})


        eps.append({"endpoint": "/api/backtests/jobs/{job_id}/wait", "method": "GET"})


        eps.append({"endpoint": "/api/backtests/{backtest_id}/summary", "method": "GET"})


        return eps





    def setup(self, task: Task, limits: Limits, initial_state: Dict[str, Any], use_doubles: bool) -> None:


        if self.config == Config.B:


            self._load_tool_jsons()


            self.financial_entry_points = self._extract_endpoints_from_tools()


        elif self.config == Config.A:


            # No financial tools


            self.financial_entry_points = []


        else:


            raise ValueError("OpenCodeAdapter only for A or B")





    def run(self, task: Task, limits: Limits, budget_guard: BudgetGuard) -> Iterator[Dict[str, Any]]:


        yield {"type": "opencode.start", "config": self.config.value, "task_id": task.task_id}


        if self.config == Config.A:


            yield {"type": "opencode.docs_only", "task_id": task.task_id}


        elif self.config == Config.B:


            yield {"type": "opencode.financial_tools_loaded", "loaded": list(self.loaded_tools.keys())}


        yield {"type": "opencode.done", "task_id": task.task_id}





    def stop(self) -> None:


        pass





    def get_capabilities(self) -> Dict[str, Any]:


        return {"has_financial_tools": self.config == Config.B, "loads_tool_jsons": self.config == Config.B}





    def get_financial_entry_points(self) -> List[Dict[str, Any]]:


        return self.financial_entry_points








def create_adapter(config: Config) -> Adapter:


    if config == Config.C:


        return ConexusAdapter()


    elif config in (Config.A, Config.B):


        return OpenCodeAdapter(config)


    else:


        raise ValueError(f"Unsupported config {config}")

