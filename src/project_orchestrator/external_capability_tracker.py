"""Persistent progress tracking for external capability acquisition."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

class WorkState(str, Enum):
    DONE = "DONE"
    WORKING = "WORKING"
    TODO = "TODO"
    BLOCKED = "BLOCKED"

@dataclass(frozen=True)
class CapabilityWorkItem:
    name: str
    slice: str
    state: WorkState
    evidence: str = ""
    next_gate: str = ""

def validate_board(items: Iterable[CapabilityWorkItem]) -> tuple[CapabilityWorkItem, ...]:
    board = tuple(items)
    keys = [(item.name.lower(), item.slice.lower()) for item in board]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate capability work item")
    for item in board:
        if item.state is WorkState.DONE and not item.evidence:
            raise ValueError(f"DONE item lacks evidence: {item.name}/{item.slice}")
        if item.state is WorkState.WORKING and not item.next_gate:
            raise ValueError(f"WORKING item lacks next gate: {item.name}/{item.slice}")
    return board

def summarize(items: Iterable[CapabilityWorkItem]) -> dict[str, list[dict[str, str]]]:
    board = validate_board(items)
    result = {state.value: [] for state in WorkState}
    for item in board:
        result[item.state.value].append({
            "name": item.name, "slice": item.slice,
            "evidence": item.evidence, "next_gate": item.next_gate,
        })
    return result

def next_todo(items: Iterable[CapabilityWorkItem]) -> CapabilityWorkItem | None:
    board = validate_board(items)
    working = [x for x in board if x.state is WorkState.WORKING]
    if working:
        return working[0]
    return next((x for x in board if x.state is WorkState.TODO), None)
