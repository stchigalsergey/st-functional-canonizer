# -*- coding: utf-8 -*-
"""Deterministic synthetic HVAC/ST data used by the Article 4 artifact.

All programs in this module are generated examples. The module contains no
industrial project source code and does not read private corpora. It renders
small typed templates into IEC 61131-3 Structured Text, applies explicit
synthetic mutations, and validates the resulting canonical sequences.

The generated JSONL is intentionally not stored in the repository: the command
line interface can recreate it and the release manifest records its SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import sys

from st_canonizer_v2 import block_distance_norm, block_levenshtein, canonize
from st_defect_taxonomy import (
    TieBreakRule,
    build_error_profile,
    edit_script,
    matches_expected_edit_kind,
)


SCHEMA_VERSION = "article4-synthetic/v1"
GENERATOR_VERSION = "0.3.0"
DEFAULT_TIE_BREAK = TieBreakRule.HISTORICAL

# ============================================================
# 1. IR: узлы управляющей логики
# ============================================================

class Node:
    pass


class Latch(Node):
    """Защёлка аварии: var := (var OR set) AND NOT rst  (или два IF)."""
    def __init__(self, var, set_expr, rst_expr):
        self.var, self.set_expr, self.rst_expr = var, set_expr, rst_expr


class TimerCall(Node):
    def __init__(self, inst, in_expr, pt):
        self.inst, self.in_expr, self.pt = inst, in_expr, pt


class EdgeCall(Node):
    def __init__(self, inst, clk):
        self.inst, self.clk = inst, clk


class FeedbackMonitor(Node):
    """TON + IF inst.Q THEN fault := TRUE (контроль обратной связи)."""
    def __init__(self, inst, in_expr, pt, fault_var):
        self.inst, self.in_expr, self.pt, self.fault_var = inst, in_expr, pt, fault_var


class Interlock(Node):
    """Защитная ветка: IF cond THEN body END_IF (пожар/замораживание/прочее)."""
    def __init__(self, kind, cond, body):
        self.kind, self.cond, self.body = kind, cond, body  # kind: FIRE|FROST|GUARD


class SetOut(Node):
    """Запись выхода: safe=True -> FALSE/0 (безопасное), False -> TRUE/!=0."""
    def __init__(self, var, safe, value=None):
        self.var, self.safe, self.value = var, safe, value


class AssignLimit(Node):
    def __init__(self, var, lo, expr, hi):
        self.var, self.lo, self.expr, self.hi = var, lo, expr, hi


class Assign(Node):
    def __init__(self, var, expr):
        self.var, self.expr = var, expr


class ResetIf(Node):
    """IF rst THEN latch_var := FALSE; END_IF (ручной сброс защёлки)."""
    def __init__(self, cond, latch_var):
        self.cond, self.latch_var = cond, latch_var


class Fsm(Node):
    """CASE sel OF  lbl: body ...  END_CASE (sel содержит mode/step/state)."""
    def __init__(self, sel, cases, els=None):
        self.sel, self.cases, self.els = sel, cases, els


class IfGuard(Node):
    def __init__(self, cond, body, els=None):
        self.cond, self.body, self.els = cond, body, els


class DisableGuard(Node):
    """IF NOT enable THEN <безопасные выходы>; RETURN; END_IF"""
    def __init__(self, enable, body):
        self.enable, self.body = enable, body


# ============================================================
# 2. Рендер IR -> ST (со стилевыми вариантами для эквивалентов)
# ============================================================

class Style:
    def __init__(self, latch_two_if=False, rename=None, zero="0.0"):
        self.latch_two_if = latch_two_if
        self.rename = rename or {}
        self.zero = zero

    def nm(self, name):
        return self.rename.get(name, name)

    def re_expr(self, e):
        """Переименование идентификаторов в строковом выражении (по словам)."""
        import re as _re
        def sub(m):
            w = m.group(0)
            return self.rename.get(w, w)
        return _re.sub(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*",
                       lambda m: ".".join(self.rename.get(p, p) for p in m.group(0).split(".")),
                       e)


def render(nodes, st, ind=0):
    pad = "    " * ind
    out = []
    for n in nodes:
        if isinstance(n, Latch):
            v, s, r = st.nm(n.var), st.re_expr(n.set_expr), st.re_expr(n.rst_expr)
            if st.latch_two_if:
                out.append(f"{pad}IF {s} THEN")
                out.append(f"{pad}    {v} := TRUE;")
                out.append(f"{pad}END_IF")
                out.append(f"{pad}IF {r} THEN")
                out.append(f"{pad}    {v} := FALSE;")
                out.append(f"{pad}END_IF")
            else:
                inner = f"{v} OR {s}"
                out.append(f"{pad}{v} := ({inner}) AND NOT {r};")
        elif isinstance(n, TimerCall):
            out.append(f"{pad}{st.nm(n.inst)}(IN := {st.re_expr(n.in_expr)}, PT := {n.pt});")
        elif isinstance(n, EdgeCall):
            out.append(f"{pad}{st.nm(n.inst)}(CLK := {st.re_expr(n.clk)});")
        elif isinstance(n, FeedbackMonitor):
            out.append(f"{pad}{st.nm(n.inst)}(IN := {st.re_expr(n.in_expr)}, PT := {n.pt});")
            out.append(f"{pad}IF {st.nm(n.inst)}.Q THEN")
            out.append(f"{pad}    {st.nm(n.fault_var)} := TRUE;")
            out.append(f"{pad}END_IF")
        elif isinstance(n, Interlock):
            out.append(f"{pad}IF {st.re_expr(n.cond)} THEN")
            out.extend(render(n.body, st, ind + 1))
            out.append(f"{pad}END_IF")
        elif isinstance(n, SetOut):
            v = st.nm(n.var)
            if n.value is not None:
                val = st.re_expr(str(n.value))
            elif n.safe:
                val = "FALSE" if not n.var[0] in "ra" else st.zero
                # bool-выходы (b/do/x/q) -> FALSE; вещественные/аналоговые (r/ao) -> 0.0
                if n.var[:2] in ("ao",) or n.var[0] == "r":
                    val = st.zero
                else:
                    val = "FALSE"
            else:
                if n.var[:2] in ("ao",) or n.var[0] == "r":
                    val = "100.0"
                else:
                    val = "TRUE"
            out.append(f"{pad}{v} := {val};")
        elif isinstance(n, AssignLimit):
            out.append(f"{pad}{st.nm(n.var)} := LIMIT({n.lo}, {st.re_expr(n.expr)}, {n.hi});")
        elif isinstance(n, Assign):
            out.append(f"{pad}{st.nm(n.var)} := {st.re_expr(n.expr)};")
        elif isinstance(n, ResetIf):
            out.append(f"{pad}IF {st.re_expr(n.cond)} THEN")
            out.append(f"{pad}    {st.nm(n.latch_var)} := FALSE;")
            out.append(f"{pad}END_IF")
        elif isinstance(n, Fsm):
            out.append(f"{pad}CASE {st.nm(n.sel)} OF")
            for lbl, body in n.cases:
                out.append(f"{pad}    {lbl}:")
                out.extend(render(body, st, ind + 2))
            if n.els is not None:
                out.append(f"{pad}    ELSE")
                out.extend(render(n.els, st, ind + 2))
            out.append(f"{pad}END_CASE")
        elif isinstance(n, IfGuard):
            out.append(f"{pad}IF {st.re_expr(n.cond)} THEN")
            out.extend(render(n.body, st, ind + 1))
            if n.els:
                out.append(f"{pad}ELSE")
                out.extend(render(n.els, st, ind + 1))
            out.append(f"{pad}END_IF")
        elif isinstance(n, DisableGuard):
            out.append(f"{pad}IF NOT {st.nm(n.enable)} THEN")
            out.extend(render(n.body, st, ind + 1))
            out.append(f"{pad}    RETURN;")
            out.append(f"{pad}END_IF")
        else:
            raise TypeError(type(n))
    return out


def render_unit(kind, name, decls, nodes, st):
    lines = [f"{kind} {name}"]
    for sec, items in decls:
        lines.append(sec)
        for d in items:
            nm = st.nm(d[0])
            lines.append(f"    {nm} : {d[1]};")
        lines.append("END_VAR")
    lines.append("")
    lines.extend(render(nodes, st, 0))
    lines.append("")
    lines.append("END_" + kind)
    return "\n".join(lines)


# ============================================================
# 3. Мутации IR по таксономии дефектов (статья 4)
# ============================================================

def clone_nodes(nodes):
    import copy
    return copy.deepcopy(nodes)


def walk_nodes(nodes):
    """(container, index, node) по всем уровням."""
    for i, n in enumerate(nodes):
        yield nodes, i, n
        for attr in ("body", "els"):
            sub = getattr(n, attr, None)
            if isinstance(sub, list):
                yield from walk_nodes(sub)
        if isinstance(n, Fsm):
            for _, body in n.cases:
                yield from walk_nodes(body)


def mut_invert_output(nodes):
    """ВЫХОД-БЕЗОПАСНЫЙ -> ВЫХОД-АКТИВНЫЙ внутри защитной ветки (инверсия)."""
    ns = clone_nodes(nodes)
    for cont, i, n in walk_nodes(ns):
        if isinstance(n, Interlock):
            for c2, j, m in walk_nodes(n.body):
                if isinstance(m, SetOut) and m.safe:
                    m.safe = False
                    return ns, "инверсия выхода в защитной ветке (безопасное -> активное)"
    return None, None


def mut_drop_protection(nodes):
    """Удаление защитной ветки целиком (пожар/замораживание)."""
    ns = clone_nodes(nodes)
    for cont, i, n in walk_nodes(ns):
        if isinstance(n, Interlock) and n.kind in ("FIRE", "FROST"):
            del cont[i]
            kind = "пожарная блокировка" if n.kind == "FIRE" else "защита от замораживания"
            return ns, f"удалена {kind}"
    return None, None


def mut_drop_reset(nodes):
    """Удаление сброса: ResetIf либо reset-члена защёлки."""
    ns = clone_nodes(nodes)
    for cont, i, n in walk_nodes(ns):
        if isinstance(n, ResetIf):
            del cont[i]
            return ns, "удалён ручной сброс защёлки"
    for cont, i, n in walk_nodes(ns):
        if isinstance(n, Latch):
            cont[i] = Assign(n.var, f"{n.var} OR {n.set_expr}")
            return ns, "из защёлки удалено звено сброса (авария не снимается)"
    return None, None


def mut_drop_feedback(nodes):
    """Удаление контроля обратной связи (таймерное подтверждение)."""
    ns = clone_nodes(nodes)
    for cont, i, n in walk_nodes(ns):
        if isinstance(n, FeedbackMonitor):
            del cont[i]
            return ns, "удалён контроль обратной связи по таймеру"
    return None, None


def mut_drop_limit(nodes):
    """LIMIT(...) -> прямое присваивание (снято ограничение)."""
    ns = clone_nodes(nodes)
    for cont, i, n in walk_nodes(ns):
        if isinstance(n, AssignLimit):
            cont[i] = Assign(n.var, n.expr)
            return ns, "снято ограничение диапазона (LIMIT удалён)"
    return None, None


def mut_extra_write(nodes):
    """Вставка несанкционированной записи активного состояния выхода."""
    ns = clone_nodes(nodes)
    outs = [n.var for _, _, n in walk_nodes(ns) if isinstance(n, SetOut)]
    if not outs:
        return None, None
    ns.append(SetOut(outs[0], safe=False))
    return ns, "вставлена лишняя запись активного состояния выхода в конце цикла"


def mut_swap_order(nodes):
    """Защита ПОСЛЕ действия вместо ДО (перестановка)."""
    ns = clone_nodes(nodes)
    idx_prot = None
    for i, n in enumerate(ns):
        if isinstance(n, Interlock) and n.kind in ("FIRE", "FROST"):
            idx_prot = i
            break
    if idx_prot is None or idx_prot >= len(ns) - 1:
        return None, None
    prot = ns.pop(idx_prot)
    ns.append(prot)
    return ns, "защитная ветка перенесена в конец (защита после действия)"


def mut_fsm_wrong_state(nodes):
    """Неверная константа следующего шага в конечном автомате."""
    ns = clone_nodes(nodes)
    for cont, i, n in walk_nodes(ns):
        if isinstance(n, Fsm):
            for lbl, body in n.cases:
                for c2, j, m in walk_nodes(body):
                    if isinstance(m, Assign) and m.var == n.sel:
                        try:
                            m.expr = str(int(m.expr) + 10)
                        except ValueError:
                            continue
                        return ns, f"неверный переход конечного автомата в шаге {lbl}"
    return None, None


MUTATIONS = [
    ("ЗАМЕНА:ВЫХОД-БЕЗОПАСНЫЙ->ВЫХОД-АКТИВНЫЙ", "инверсия поведения", mut_invert_output),
    ("УДАЛЕНИЕ:ЗАЩИТА/БЛОКИРОВКА", "пропуск обязательного шага", mut_drop_protection),
    ("УДАЛЕНИЕ:ЗАЩЁЛКА/СБРОС", "пропуск обязательного шага", mut_drop_reset),
    ("УДАЛЕНИЕ:КОНТРОЛЬ-ОС", "пропуск обязательного шага", mut_drop_feedback),
    ("УДАЛЕНИЕ:ГАРД/ОГРАНИЧЕНИЕ", "пропуск обязательного шага", mut_drop_limit),
    ("ВСТАВКА:ВЫХОД-АКТИВНЫЙ", "лишнее действие", mut_extra_write),
    ("ПЕРЕСТАНОВКА:ЗАЩИТА", "нарушение порядка", mut_swap_order),
    ("ЗАМЕНА:ПЕРЕХОД-FSM", "инверсия поведения", mut_fsm_wrong_state),
]


# ============================================================
# 4. Шаблоны компонентов (категории A/B/C/D/E)
# ============================================================
# Все имена сохраняют доменные подстроки (fire/frost/twater/step/mode),
# чтобы работали доменные распознаватели канонизатора.

def tpl_fan_contactor(p):
    """A: вентилятор с контактором: защёлка, контроль ОС, пожар, сброс."""
    fb = p["fb_t"]; guard = p["guard"]
    decls = [
        ("VAR_INPUT", [("bEnable", "BOOL"), ("bCmdStart", "BOOL"), ("diRunFb", "BOOL"),
                       ("bFireAlarm", "BOOL"), ("bReset", "BOOL")]),
        ("VAR_OUTPUT", [("doStart", "BOOL"), ("bFault", "BOOL")]),
        ("VAR", [("tonFb", "TON"), ("rtReset", "R_TRIG")]),
    ]
    nodes = [
        EdgeCall("rtReset", "bReset"),
        Interlock("FIRE", "bFireAlarm", [SetOut("doStart", safe=True)]),
        FeedbackMonitor("tonFb", "doStart AND NOT diRunFb", f"T#{fb}s", "bFault"),
        Latch("bFault", "tonFb.Q", "rtReset.Q"),
        IfGuard("bCmdStart AND NOT bFault AND NOT bFireAlarm",
                [SetOut("doStart", safe=False)],
                [SetOut("doStart", safe=True)]),
    ]
    g_txt = ""
    if guard:
        nodes.insert(1, DisableGuard("bEnable",
                                     [SetOut("doStart", safe=True),
                                      SetOut("bFault", safe=True)]))
        g_txt = ("при снятом разрешении bEnable обнулить выходы и выйти из блока; ")
    ru = (f"Разработать функциональный блок управления приточным вентилятором с контактором. "
          f"Входы: bEnable — разрешение, bCmdStart — команда пуска, diRunFb — обратная связь "
          f"от пускателя, bFireAlarm — сигнал пожарной сигнализации, bReset — сброс аварий. "
          f"Выходы: doStart — пуск вентилятора, bFault — авария. "
          f"Требования: {g_txt}при пожаре немедленно снять пуск; контролировать обратную связь "
          f"таймером {fb} с — при отсутствии подтверждения зафиксировать аварию защёлкой; "
          f"авария снимается только командой сброса (по фронту); пуск разрешён при отсутствии "
          f"аварии и пожара.")
    return "FB_FanCont", decls, nodes, ru


def tpl_recup_plate(p):
    """A: пластинчатый рекуператор: защита от обмерзания байпасом."""
    ft = p["frost_t"]; analog = p["analog"]
    decls = [
        ("VAR_INPUT", [("bEnable", "BOOL"), ("rTFrostExh", "REAL"), ("rBypassSp", "REAL")]),
        ("VAR_OUTPUT", [("aoBypass", "REAL"), ("doBypass", "BOOL"), ("bFrostRisk", "BOOL")]),
        ("VAR", []),
    ]
    if analog:
        act_frost = [SetOut("bFrostRisk", safe=False), Assign("aoBypass", "100.0")]
        act_norm = [AssignLimit("aoBypass", "0.0", "rBypassSp", "100.0")]
        act_off = [SetOut("aoBypass", safe=True), SetOut("bFrostRisk", safe=True)]
        io_txt = "aoBypass — аналоговый байпас 0–100 %"
    else:
        act_frost = [SetOut("bFrostRisk", safe=False), SetOut("doBypass", safe=False)]
        act_norm = [SetOut("doBypass", safe=True)]
        act_off = [SetOut("doBypass", safe=True), SetOut("bFrostRisk", safe=True)]
        io_txt = "doBypass — дискретный байпас"
    nodes = [
        DisableGuard("bEnable", act_off),
        Interlock("FROST", f"rTFrostExh < {ft}.0", act_frost),
        IfGuard("NOT bFrostRisk", act_norm),
    ]
    ru = (f"Разработать функциональный блок защиты пластинчатого рекуператора от обмерзания. "
          f"Входы: bEnable — разрешение, rTFrostExh — температура вытяжки за рекуператором, "
          f"rBypassSp — уставка байпаса. Выходы: {io_txt}, bFrostRisk — угроза обмерзания. "
          f"Требования: при температуре ниже {ft} °C зафиксировать угрозу и полностью открыть "
          f"байпас; в норме отрабатывать уставку; при снятом разрешении — безопасное состояние.")
    return "FB_RecupPlate", decls, nodes, ru


def tpl_recup_rotary(p):
    """A: роторный рекуператор: скорость с LIMIT, защита от обмерзания."""
    ft = p["frost_t"]; hi = p["hi"]
    decls = [
        ("VAR_INPUT", [("bEnable", "BOOL"), ("rSpRotor", "REAL"), ("rTFrostExh", "REAL")]),
        ("VAR_OUTPUT", [("aoRotor", "REAL"), ("bFrostRisk", "BOOL")]),
        ("VAR", []),
    ]
    nodes = [
        DisableGuard("bEnable", [SetOut("aoRotor", safe=True), SetOut("bFrostRisk", safe=True)]),
        Interlock("FROST", f"rTFrostExh < {ft}.0",
                  [SetOut("bFrostRisk", safe=False), Assign("aoRotor", "20.0")]),
        IfGuard("NOT bFrostRisk", [AssignLimit("aoRotor", "0.0", "rSpRotor", f"{hi}.0")]),
    ]
    ru = (f"Разработать функциональный блок управления роторным рекуператором. Входы: bEnable — "
          f"разрешение, rSpRotor — уставка скорости ротора, rTFrostExh — температура вытяжки. "
          f"Выходы: aoRotor — скорость ротора, bFrostRisk — угроза обмерзания. Требования: при "
          f"температуре ниже {ft} °C зафиксировать угрозу и снизить скорость до 20 %; в норме "
          f"ограничивать уставку диапазоном 0–{hi} %; при снятом разрешении остановить ротор.")
    return "FB_RecupRotor", decls, nodes, ru


def tpl_water_cooler(p):
    """A: водяной охладитель: клапан с LIMIT, блокировка по конденсату."""
    guard = p["condens"]; hi = p["hi"]
    decls = [
        ("VAR_INPUT", [("bEnable", "BOOL"), ("rCoolSp", "REAL"), ("diCondens", "BOOL")]),
        ("VAR_OUTPUT", [("aoCoolValve", "REAL"), ("doCoolPump", "BOOL")]),
        ("VAR", []),
    ]
    nodes = [
        DisableGuard("bEnable", [SetOut("aoCoolValve", safe=True),
                                 SetOut("doCoolPump", safe=True)]),
        AssignLimit("aoCoolValve", "0.0", "rCoolSp", f"{hi}.0"),
        SetOut("doCoolPump", safe=False),
    ]
    c_txt = ""
    if guard:
        nodes.insert(1, Interlock("GUARD", "diCondens",
                                  [SetOut("aoCoolValve", safe=True),
                                   SetOut("doCoolPump", safe=True)]))
        c_txt = ("при срабатывании датчика переполнения поддона конденсата diCondens закрыть "
                 "клапан и отключить насос; ")
    ru = (f"Разработать функциональный блок управления водяным охладителем. Входы: bEnable — "
          f"разрешение, rCoolSp — уставка клапана, diCondens — датчик конденсата. Выходы: "
          f"aoCoolValve — клапан 0–{hi} %, doCoolPump — насос контура. Требования: {c_txt}"
          f"уставку ограничивать диапазоном 0–{hi} %; при снятом разрешении — безопасное состояние.")
    return "FB_WaterCooler", decls, nodes, ru


def tpl_uv(p):
    """A: УФ-стерилизатор: разрешение только при потоке, задержка включения."""
    dl = p["delay"]
    decls = [
        ("VAR_INPUT", [("bUvDemand", "BOOL"), ("bAirFlow", "BOOL")]),
        ("VAR_OUTPUT", [("doUvLamp", "BOOL")]),
        ("VAR", [("tonUvDelay", "TON")]),
    ]
    nodes = [
        Interlock("GUARD", "NOT bAirFlow", [SetOut("doUvLamp", safe=True)]),
        TimerCall("tonUvDelay", "bUvDemand AND bAirFlow", f"T#{dl}s"),
        IfGuard("tonUvDelay.Q", [SetOut("doUvLamp", safe=False)]),
    ]
    ru = (f"Разработать функциональный блок управления УФ-стерилизатором канала. Входы: "
          f"bUvDemand — запрос работы, bAirFlow — подтверждение потока воздуха. Выход doUvLamp — "
          f"включение лампы. Требования: без потока воздуха лампа запрещена (немедленное "
          f"отключение); включать с задержкой {dl} с после появления запроса и потока.")
    return "FB_UvSteril", decls, nodes, ru


def tpl_pump(p):
    """A: циркуляционный насос: контроль ОС + защита по сухому ходу."""
    fb = p["fb_t"]; dry = p["dry"]
    decls = [
        ("VAR_INPUT", [("bCmdPump", "BOOL"), ("diPumpFb", "BOOL"), ("diDryLevel", "BOOL"),
                       ("bReset", "BOOL")]),
        ("VAR_OUTPUT", [("doPump", "BOOL"), ("bFault", "BOOL")]),
        ("VAR", [("tonPumpFb", "TON"), ("rtReset", "R_TRIG")]),
    ]
    nodes = [
        EdgeCall("rtReset", "bReset"),
        FeedbackMonitor("tonPumpFb", "doPump AND NOT diPumpFb", f"T#{fb}s", "bFault"),
        ResetIf("rtReset.Q", "bFault"),
        IfGuard("bCmdPump AND NOT bFault", [SetOut("doPump", safe=False)],
                [SetOut("doPump", safe=True)]),
    ]
    d_txt = ""
    if dry:
        nodes.insert(1, Latch("bFault", "diDryLevel", "rtReset.Q"))
        d_txt = "сухой ход (diDryLevel) фиксировать защёлкой аварии; "
    ru = (f"Разработать функциональный блок управления циркуляционным насосом. Входы: bCmdPump — "
          f"команда, diPumpFb — обратная связь, diDryLevel — датчик сухого хода, bReset — сброс. "
          f"Выходы: doPump — пуск насоса, bFault — авария. Требования: {d_txt}контроль обратной "
          f"связи таймером {fb} с; сброс по фронту bReset; пуск только без аварии.")
    return "FB_CircPump", decls, nodes, ru


def tpl_supply_cascade(p):
    """A: выбор уставки притока по каскаду комнатной температуры."""
    lo = p["lo"]; hi = p["hi"]
    decls = [
        ("VAR_INPUT", [("bEnable", "BOOL"), ("rTRoom", "REAL"), ("rTRoomSp", "REAL"),
                       ("rKCascade", "REAL")]),
        ("VAR_OUTPUT", [("rTSupplySp", "REAL")]),
        ("VAR", [("rDelta", "REAL")]),
    ]
    nodes = [
        DisableGuard("bEnable", [Assign("rTSupplySp", f"{lo}.0")]),
        Assign("rDelta", "(rTRoomSp - rTRoom) * rKCascade"),
        AssignLimit("rTSupplySp", f"{lo}.0", "rTRoomSp + rDelta", f"{hi}.0"),
    ]
    ru = (f"Разработать функциональный блок каскадного формирования уставки приточной "
          f"температуры по комнатной. Входы: bEnable — разрешение, rTRoom — комнатная "
          f"температура, rTRoomSp — её уставка, rKCascade — коэффициент каскада. Выход "
          f"rTSupplySp — уставка притока. Требования: приточная уставка = комнатная уставка + "
          f"(рассогласование × коэффициент), с ограничением диапазоном {lo}–{hi} °C; при снятом "
          f"разрешении выдавать минимум {lo} °C.")
    return "FB_CascadeSp", decls, nodes, ru


def tpl_fan_vfd(p):
    """A: вентилятор с ПЧ: уставка с LIMIT, пожар, контроль ОС."""
    fb = p["fb_t"]; lo = p["lo"]; hi = p["hi"]
    decls = [
        ("VAR_INPUT", [("bEnable", "BOOL"), ("rSpeedSp", "REAL"), ("diVfdRun", "BOOL"),
                       ("bFireAlarm", "BOOL"), ("bReset", "BOOL")]),
        ("VAR_OUTPUT", [("aoSpeed", "REAL"), ("doRun", "BOOL"), ("bFault", "BOOL")]),
        ("VAR", [("tonFb", "TON"), ("rtReset", "R_TRIG")]),
    ]
    nodes = [
        EdgeCall("rtReset", "bReset"),
        DisableGuard("bEnable", [SetOut("aoSpeed", safe=True), SetOut("doRun", safe=True)]),
        Interlock("FIRE", "bFireAlarm",
                  [SetOut("doRun", safe=True), SetOut("aoSpeed", safe=True)]),
        FeedbackMonitor("tonFb", "doRun AND NOT diVfdRun", f"T#{fb}s", "bFault"),
        ResetIf("rtReset.Q", "bFault"),
        IfGuard("NOT bFault AND NOT bFireAlarm",
                [SetOut("doRun", safe=False)]),
        AssignLimit("aoSpeed", f"{lo}.0", "rSpeedSp", f"{hi}.0"),
    ]
    ru = (f"Разработать функциональный блок управления вентилятором с преобразователем частоты. "
          f"Входы: bEnable — разрешение работы, rSpeedSp — уставка скорости, "
          f"diVfdRun — подтверждение работы ПЧ, bFireAlarm — пожар, bReset — сброс. "
          f"Выходы: aoSpeed — задание скорости, doRun — команда работы, bFault — авария. "
          f"Требования: при снятом разрешении обнулить выходы и выйти; при пожаре остановить; "
          f"контроль подтверждения работы таймером {fb} с с фиксацией аварии; сброс аварии по "
          f"фронту bReset; уставку скорости ограничивать диапазоном {lo}–{hi} %.")
    return "FB_FanVfd", decls, nodes, ru


def tpl_air_filter(p):
    """A: фильтр: засорение по перепаду с таймером, защёлка, ручной/авто сброс."""
    ct = p["clog_t"]; autoclear = p["autoclear"]
    decls = [
        ("VAR_INPUT", [("bEnable", "BOOL"), ("bFanRun", "BOOL"), ("diDiffPress", "BOOL"),
                       ("bReset", "BOOL")]),
        ("VAR_OUTPUT", [("bClogged", "BOOL")]),
        ("VAR", [("tonClog", "TON"), ("tonClear", "TON"), ("rtReset", "R_TRIG")]),
    ]
    nodes = [
        EdgeCall("rtReset", "bReset"),
        DisableGuard("bEnable", [SetOut("bClogged", safe=True)]),
        TimerCall("tonClog", "bFanRun AND diDiffPress AND NOT bClogged", f"T#{ct}s"),
        IfGuard("tonClog.Q", [SetOut("bClogged", safe=False)]),
        ResetIf("rtReset.Q", "bClogged"),
    ]
    a_txt = ""
    if autoclear:
        nodes.insert(4, TimerCall("tonClear", "bFanRun AND NOT diDiffPress AND bClogged",
                                  f"T#{ct}s"))
        nodes.insert(5, IfGuard("tonClear.Q", [SetOut("bClogged", safe=True)]))
        a_txt = (f"автоматически снимать засорение, если при работающем вентиляторе перепад "
                 f"отсутствует дольше {ct} с (фильтр заменили); ")
    ru = (f"Разработать функциональный блок контроля засорения воздушного фильтра. "
          f"Входы: bEnable — разрешение, bFanRun — вентилятор работает, diDiffPress — "
          f"дискретный датчик перепада давления, bReset — сброс. Выход bClogged — фильтр засорён. "
          f"Требования: фиксировать засорение защёлкой, если при работающем вентиляторе перепад "
          f"держится дольше {ct} с; {a_txt}сбрасывать по фронту bReset; при снятом разрешении "
          f"сбросить состояние и выйти.")
    return "FB_AirFilter", decls, nodes, ru


def tpl_water_heater(p):
    """A: водяной калорифер: защита от замораживания, насос, клапан с LIMIT."""
    tw = p["t_water"]
    decls = [
        ("VAR_INPUT", [("bEnable", "BOOL"), ("rTWater", "REAL"), ("rValveSp", "REAL"),
                       ("bFireAlarm", "BOOL")]),
        ("VAR_OUTPUT", [("aoValve", "REAL"), ("doPump", "BOOL"), ("bFrostThreat", "BOOL")]),
        ("VAR", []),
    ]
    nodes = [
        DisableGuard("bEnable", [SetOut("aoValve", safe=True), SetOut("doPump", safe=True)]),
        Interlock("FROST", f"rTWater < {tw}.0",
                  [SetOut("bFrostThreat", safe=False), SetOut("doPump", safe=False),
                   Assign("aoValve", "100.0")]),
        Interlock("FIRE", "bFireAlarm", [SetOut("doPump", safe=True)]),
        IfGuard("NOT bFrostThreat",
                [AssignLimit("aoValve", "0.0", "rValveSp", "100.0"),
                 SetOut("doPump", safe=False)]),
    ]
    ru = (f"Разработать функциональный блок управления водяным калорифером приточной установки. "
          f"Входы: bEnable — разрешение, rTWater — температура обратной воды, rValveSp — уставка "
          f"клапана, bFireAlarm — пожар. Выходы: aoValve — клапан 0–100 %, doPump — насос, "
          f"bFrostThreat — угроза замораживания. Требования: при температуре обратной воды ниже "
          f"{tw} °C зафиксировать угрозу замораживания, полностью открыть клапан и включить насос; "
          f"при пожаре отключить насос; в норме ограничивать уставку клапана диапазоном 0–100 %.")
    return "FB_WaterHeater", decls, nodes, ru


def tpl_damper(p):
    """A: воздушная заслонка: открытие с подтверждением концевиком, пожар."""
    ot = p["open_t"]
    decls = [
        ("VAR_INPUT", [("bCmdOpen", "BOOL"), ("diOpenEnd", "BOOL"), ("bFireAlarm", "BOOL"),
                       ("bReset", "BOOL")]),
        ("VAR_OUTPUT", [("doOpen", "BOOL"), ("bFault", "BOOL")]),
        ("VAR", [("tonOpen", "TON"), ("rtReset", "R_TRIG")]),
    ]
    nodes = [
        EdgeCall("rtReset", "bReset"),
        Interlock("FIRE", "bFireAlarm", [SetOut("doOpen", safe=True)]),
        FeedbackMonitor("tonOpen", "doOpen AND NOT diOpenEnd", f"T#{ot}s", "bFault"),
        ResetIf("rtReset.Q", "bFault"),
        IfGuard("bCmdOpen AND NOT bFault AND NOT bFireAlarm",
                [SetOut("doOpen", safe=False)],
                [SetOut("doOpen", safe=True)]),
    ]
    ru = (f"Разработать функциональный блок управления воздушной заслонкой с электроприводом. "
          f"Входы: bCmdOpen — команда открытия, diOpenEnd — концевик «открыто», bFireAlarm — "
          f"пожар, bReset — сброс. Выходы: doOpen — привод открытия, bFault — авария привода. "
          f"Требования: при пожаре закрыть заслонку; если за {ot} с после команды концевик не "
          f"подтвердил открытие — авария (защёлка, сброс по фронту bReset); открытие разрешено "
          f"без аварии и пожара.")
    return "FB_Damper", decls, nodes, ru


def tpl_eheater(p):
    """A: электрокалорифер (1-3 ступени): продув, перегрев с защёлкой."""
    cd = p["cool_t"]; stages = p["stages"]
    outs = [("doHeater" if stages == 1 else f"doHeater{k+1}") for k in range(stages)]
    decls = [
        ("VAR_INPUT", [("bDemand", "BOOL"), ("bAirFlow", "BOOL"), ("diOverheat", "BOOL"),
                       ("bReset", "BOOL")] +
                      ([("rHeatDemand", "REAL")] if stages > 1 else [])),
        ("VAR_OUTPUT", [(o, "BOOL") for o in outs] + [("bOverheatFault", "BOOL")]),
        ("VAR", [("tonCool", "TON"), ("rtReset", "R_TRIG")]),
    ]
    nodes = [
        EdgeCall("rtReset", "bReset"),
        Latch("bOverheatFault", "diOverheat", "rtReset.Q"),
        Interlock("GUARD", "NOT bAirFlow", [SetOut(o, safe=True) for o in outs]),
        TimerCall("tonCool", f"NOT bDemand AND {outs[0]}", f"T#{cd}s"),
    ]
    if stages == 1:
        nodes.append(IfGuard("bDemand AND bAirFlow AND NOT bOverheatFault",
                             [SetOut(outs[0], safe=False)]))
        st_txt = "одну ступень нагрева"
    else:
        thresholds = [30, 60, 90][:stages]
        for k, o in enumerate(outs):
            nodes.append(IfGuard(
                f"bDemand AND bAirFlow AND NOT bOverheatFault AND rHeatDemand > {thresholds[k]}.0",
                [SetOut(o, safe=False)],
                [SetOut(o, safe=True)]))
        st_txt = (f"{stages} ступени нагрева, включаемые по величине запроса rHeatDemand "
                  f"(пороги {', '.join(str(t) for t in thresholds)} %)")
    nodes.append(IfGuard("tonCool.Q", [SetOut(o, safe=True) for o in outs]))
    ru = (f"Разработать функциональный блок управления электрокалорифером ({st_txt}). Входы: "
          f"bDemand — запрос нагрева, bAirFlow — подтверждение потока воздуха, diOverheat — "
          f"термостат перегрева, bReset — сброс. Требования: перегрев фиксировать защёлкой со "
          f"сбросом по фронту; без потока воздуха нагрев запрещён; после снятия запроса выдержать "
          f"продув {cd} с и отключить все ступени.")
    return "FB_EHeater", decls, nodes, ru


def tpl_fsm_start(p):
    """C: конечный автомат пуска установки (заслонка -> вентилятор -> работа).
    Варианты: basic; warmup (+шаг прогрева калорифера); purge (+шаг продувки)."""
    n_wait = p["wait_t"]; variant = p["variant"]
    ins = [("bCmdRun", "BOOL"), ("diDamperEnd", "BOOL"), ("diFanRun", "BOOL"),
           ("bFireAlarm", "BOOL")]
    outs = [("doDamper", "BOOL"), ("doFan", "BOOL"), ("bRunning", "BOOL")]
    lvars = [("iStep", "INT"), ("tonWait", "TON")]
    if variant == "warmup":
        ins.append(("rTSupply", "REAL"))
        outs.append(("aoValve", "REAL"))
    if variant == "purge":
        outs.append(("doHeater", "BOOL"))
        lvars.append(("tonPurge", "TON"))

    cases = [
        ("0", [SetOut("bRunning", safe=True),
               IfGuard("bCmdRun AND NOT bFireAlarm", [Assign("iStep", "10")])]),
    ]
    if variant == "warmup":
        cases.append(("10", [Assign("aoValve", "100.0"),
                             IfGuard("rTSupply > 20.0", [Assign("iStep", "15")])]))
        cases.append(("15", [SetOut("doDamper", safe=False),
                             IfGuard("diDamperEnd", [Assign("iStep", "20")])]))
        warm_txt = ("; перед открытием заслонки прогреть калорифер (клапан 100 % до "
                    "температуры притока выше 20 °C)")
    else:
        cases.append(("10", [SetOut("doDamper", safe=False),
                             IfGuard("diDamperEnd", [Assign("iStep", "20")])]))
        warm_txt = ""
    cases.append(("20", [SetOut("doFan", safe=False),
                         TimerCall("tonWait", "NOT diFanRun", f"T#{n_wait}s"),
                         IfGuard("diFanRun", [Assign("iStep", "30")])]))
    cases.append(("30", [SetOut("bRunning", safe=False),
                         IfGuard("NOT bCmdRun", [Assign("iStep", "40")])]))
    if variant == "purge":
        cases.append(("40", [SetOut("doHeater", safe=True),
                             TimerCall("tonPurge", "diFanRun", "T#60s"),
                             IfGuard("tonPurge.Q", [Assign("iStep", "50")])]))
        cases.append(("50", [SetOut("doFan", safe=True), SetOut("doDamper", safe=True),
                             Assign("iStep", "0")]))
        purge_txt = "; при останове сначала отключить нагрев и продуть 60 с, затем остановить"
    else:
        cases.append(("40", [SetOut("doFan", safe=True), SetOut("doDamper", safe=True),
                             Assign("iStep", "0")]))
        purge_txt = ""
    fire_off = [SetOut("doFan", safe=True), SetOut("doDamper", safe=True),
                Assign("iStep", "0")]
    nodes = [Interlock("FIRE", "bFireAlarm", fire_off), Fsm("iStep", cases)]
    decls = [("VAR_INPUT", ins), ("VAR_OUTPUT", outs), ("VAR", lvars)]
    ru = (f"Разработать программу пуска приточной установки конечным автоматом (шаги через 10): "
          f"ожидание команды; открыть заслонку и ждать концевик; пустить вентилятор и ждать "
          f"подтверждение (контроль {n_wait} с); работа{warm_txt}{purge_txt}. "
          f"При пожаре — немедленный останов вентилятора, закрытие заслонки и переход в шаг 0.")
    return "PRG_StartFsm", decls, nodes, ru


def tpl_fsm_stop(p):
    """C: автомат останова с выбегом и дозакрытием заслонки."""
    coast = p["coast_t"]
    decls = [
        ("VAR_INPUT", [("bCmdStop", "BOOL"), ("diFanRun", "BOOL"), ("bFireAlarm", "BOOL")]),
        ("VAR_OUTPUT", [("doFan", "BOOL"), ("doDamper", "BOOL"), ("bStopped", "BOOL")]),
        ("VAR", [("iStepStop", "INT"), ("tonCoast", "TON")]),
    ]
    nodes = [
        Interlock("FIRE", "bFireAlarm",
                  [SetOut("doFan", safe=True), SetOut("doDamper", safe=True),
                   Assign("iStepStop", "20")]),
        Fsm("iStepStop", [
            ("0", [SetOut("bStopped", safe=True),
                   IfGuard("bCmdStop", [Assign("iStepStop", "10")])]),
            ("10", [SetOut("doFan", safe=True),
                    TimerCall("tonCoast", "diFanRun", f"T#{coast}s"),
                    IfGuard("tonCoast.Q AND NOT diFanRun", [Assign("iStepStop", "20")])]),
            ("20", [SetOut("doDamper", safe=True), SetOut("bStopped", safe=False),
                    IfGuard("NOT bCmdStop", [Assign("iStepStop", "0")])]),
        ]),
    ]
    ru = (f"Разработать программу останова установки конечным автоматом: по команде снять пуск "
          f"вентилятора, дождаться останова по выбегу (контроль {coast} с), затем закрыть "
          f"заслонку и зафиксировать состояние «остановлено»; при пожаре — немедленный переход "
          f"к закрытию.")
    return "PRG_StopFsm", decls, nodes, ru


def tpl_two_fans(p):
    """D: два вентилятора: основной/резервный с переключением по аварии."""
    fb = p["fb_t"]
    decls = [
        ("VAR_INPUT", [("bCmdRun", "BOOL"), ("diRun1", "BOOL"), ("diRun2", "BOOL"),
                       ("bFireAlarm", "BOOL"), ("bReset", "BOOL")]),
        ("VAR_OUTPUT", [("doFan1", "BOOL"), ("doFan2", "BOOL"), ("bFault1", "BOOL"),
                        ("bFault2", "BOOL")]),
        ("VAR", [("tonFb1", "TON"), ("tonFb2", "TON"), ("rtReset", "R_TRIG")]),
    ]
    nodes = [
        EdgeCall("rtReset", "bReset"),
        Interlock("FIRE", "bFireAlarm",
                  [SetOut("doFan1", safe=True), SetOut("doFan2", safe=True)]),
        FeedbackMonitor("tonFb1", "doFan1 AND NOT diRun1", f"T#{fb}s", "bFault1"),
        FeedbackMonitor("tonFb2", "doFan2 AND NOT diRun2", f"T#{fb}s", "bFault2"),
        ResetIf("rtReset.Q", "bFault1"),
        ResetIf("rtReset.Q", "bFault2"),
        IfGuard("bCmdRun AND NOT bFireAlarm AND NOT bFault1",
                [SetOut("doFan1", safe=False), SetOut("doFan2", safe=True)],
                [IfGuard("bCmdRun AND NOT bFireAlarm AND NOT bFault2",
                         [SetOut("doFan2", safe=False), SetOut("doFan1", safe=True)],
                         [SetOut("doFan1", safe=True), SetOut("doFan2", safe=True)])]),
    ]
    ru = (f"Разработать функциональный блок управления парой вентиляторов (основной + резервный). "
          f"Требования: контроль обратной связи каждого таймером {fb} с с защёлкой аварии; при "
          f"аварии основного автоматически перейти на резервный; при пожаре остановить оба; "
          f"сброс аварий по фронту bReset.")
    return "FB_TwoFans", decls, nodes, ru


def tpl_full_unit(p):
    """E: приточная установка целиком: заслонка+вентилятор+калорифер(+фильтр)+пожар."""
    fb = p["fb_t"]; tw = p["t_water"]; heater = p["heater"]; has_filter = p["filter"]
    ins = [("bCmdRun", "BOOL"), ("diDamperEnd", "BOOL"), ("diFanRun", "BOOL"),
           ("rValveSp", "REAL"), ("bFireAlarm", "BOOL"), ("bReset", "BOOL")]
    outs = [("doDamper", "BOOL"), ("doFan", "BOOL"), ("aoValve", "REAL"),
            ("bFault", "BOOL")]
    lvars = [("tonFb", "TON"), ("rtReset", "R_TRIG"), ("iStep", "INT")]
    nodes = [EdgeCall("rtReset", "bReset")]

    fire_off = [SetOut("doFan", safe=True), SetOut("doDamper", safe=True),
                SetOut("aoValve", safe=True), Assign("iStep", "0")]
    if heater == "water":
        ins.append(("rTWater", "REAL"))
        outs += [("doPump", "BOOL"), ("bFrostThreat", "BOOL")]
        nodes.append(Interlock("FIRE", "bFireAlarm", fire_off))
        nodes.append(Interlock("FROST", f"rTWater < {tw}.0",
                               [SetOut("bFrostThreat", safe=False),
                                SetOut("doPump", safe=False),
                                Assign("aoValve", "100.0"), SetOut("doFan", safe=True),
                                Assign("iStep", "0")]))
        run_body = [IfGuard("NOT bFrostThreat",
                            [AssignLimit("aoValve", "0.0", "rValveSp", "100.0"),
                             SetOut("doPump", safe=False)])]
        heat_txt = (f"водяной калорифер с защитой от замораживания по обратной воде (порог "
                    f"{tw} °C: открыть клапан на 100 %, включить насос, остановить вентилятор)")
    else:
        ins.append(("diOverheat", "BOOL"))
        outs += [("doHeaterEl", "BOOL"), ("bOverheatFault", "BOOL")]
        nodes.append(Interlock("FIRE", "bFireAlarm",
                               fire_off + [SetOut("doHeaterEl", safe=True)]))
        nodes.append(Latch("bOverheatFault", "diOverheat", "rtReset.Q"))
        run_body = [IfGuard("NOT bOverheatFault AND diFanRun",
                            [SetOut("doHeaterEl", safe=False),
                             AssignLimit("aoValve", "0.0", "rValveSp", "100.0")],
                            [SetOut("doHeaterEl", safe=True)])]
        heat_txt = ("электрокалорифер с защёлкой перегрева (нагрев только при работающем "
                    "вентиляторе, авария сбрасывается по фронту)")
    if has_filter:
        ins.append(("diDiffPress", "BOOL"))
        outs.append(("bClogged", "BOOL"))
        lvars.append(("tonClog", "TON"))
        nodes.append(TimerCall("tonClog", "doFan AND diDiffPress AND NOT bClogged", "T#60s"))
        nodes.append(IfGuard("tonClog.Q", [SetOut("bClogged", safe=False)]))
        nodes.append(ResetIf("rtReset.Q", "bClogged"))
        f_txt = "; контроль засорения фильтра по перепаду (60 с, защёлка, сброс общий)"
    else:
        f_txt = ""
    nodes.append(FeedbackMonitor("tonFb", "doFan AND NOT diFanRun", f"T#{fb}s", "bFault"))
    nodes.append(ResetIf("rtReset.Q", "bFault"))
    nodes.append(Fsm("iStep", [
        ("0", [IfGuard("bCmdRun AND NOT bFireAlarm AND NOT bFault",
                       [Assign("iStep", "10")])]),
        ("10", [SetOut("doDamper", safe=False),
                IfGuard("diDamperEnd", [Assign("iStep", "20")])]),
        ("20", [SetOut("doFan", safe=False),
                IfGuard("diFanRun", [Assign("iStep", "30")])]),
        ("30", run_body + [IfGuard("NOT bCmdRun OR bFault", [Assign("iStep", "40")])]),
        ("40", [SetOut("doFan", safe=True), SetOut("doDamper", safe=True),
                SetOut("aoValve", safe=True), Assign("iStep", "0")]),
    ]))
    decls = [("VAR_INPUT", ins), ("VAR_OUTPUT", outs), ("VAR", lvars)]
    ru = (f"Разработать программу управления приточной установкой: заслонка с концевиком, "
          f"вентилятор с контролем обратной связи ({fb} с, защёлка аварии, сброс по фронту), "
          f"{heat_txt}, пожарная блокировка (останов и закрытие всего){f_txt}. "
          f"Последовательность пуска конечным автоматом: заслонка -> вентилятор -> работа с "
          f"регулированием (ограничение 0–100 %); останов по снятию команды или аварии.")
    return "PRG_AhuUnit", decls, nodes, ru


TEMPLATES = {
    "A": [(tpl_fan_contactor, {"fb_t": [3, 5, 8, 10, 15], "guard": [False, True]}),
          (tpl_fan_vfd, {"fb_t": [5, 10], "lo": [0, 20, 30], "hi": [90, 100]}),
          (tpl_air_filter, {"clog_t": [30, 60, 120, 300], "autoclear": [False, True]}),
          (tpl_water_heater, {"t_water": [10, 12, 15, 18]}),
          (tpl_damper, {"open_t": [15, 30, 45, 60, 90]}),
          (tpl_eheater, {"cool_t": [30, 60, 90, 120], "stages": [1, 2, 3]}),
          (tpl_recup_plate, {"frost_t": [1, 3, 5], "analog": [False, True]}),
          (tpl_recup_rotary, {"frost_t": [1, 3, 5], "hi": [90, 100]}),
          (tpl_water_cooler, {"condens": [False, True], "hi": [90, 100]}),
          (tpl_uv, {"delay": [10, 30, 60, 120]}),
          (tpl_pump, {"fb_t": [3, 5, 10], "dry": [False, True]}),
          (tpl_supply_cascade, {"lo": [16, 18], "hi": [28, 30, 35]})],
    "C": [(tpl_fsm_start, {"wait_t": [5, 8, 10, 15, 20],
                           "variant": ["basic", "warmup", "purge"]}),
          (tpl_fsm_stop, {"coast_t": [20, 30, 60]})],
    "D": [(tpl_two_fans, {"fb_t": [3, 5, 8, 10]})],
    "E": [(tpl_full_unit, {"fb_t": [5, 10, 15], "t_water": [10, 12, 15],
                           "heater": ["water", "electric"], "filter": [False, True]})],
}

# схемы согласованного переименования для эквивалентов
# (сохраняют доменные подстроки: fire/twater/step — см. FIRE_KW/FROST_KW/MODE_KW)
RENAME_SCHEMES = [
    {"bCmdStart": "bStartCmd", "diRunFb": "diFbRun", "bFireAlarm": "bFireSig",
     "bReset": "bAckErr", "doStart": "doFanOn", "bFault": "bErr",
     "tonFb": "tonCheck", "rtReset": "rtAck", "bEnable": "bEna",
     "rSpeedSp": "rSpSpeed", "diVfdRun": "diDrvRun", "aoSpeed": "aoSpd",
     "doRun": "doDrvRun", "bFanRun": "bFanActive", "diDiffPress": "diPressDrop",
     "bClogged": "bDirty", "tonClog": "tonDirty", "rTWater": "rTwaterRet",
     "rValveSp": "rSpValve", "aoValve": "aoHeatValve", "doPump": "doCircPump",
     "bFrostThreat": "bFrostAlert", "bCmdOpen": "bOpenCmd", "diOpenEnd": "diEndOpen",
     "doOpen": "doDrvOpen", "tonOpen": "tonEnd", "bDemand": "bHeatReq",
     "bAirFlow": "bFlowOk", "diOverheat": "diTooHot", "doHeater": "doTen",
     "bOverheatFault": "bHotFault", "tonCool": "tonPurge", "bCmdRun": "bRunCmd",
     "diDamperEnd": "diEndDamper", "diFanRun": "diFanFb", "doDamper": "doDamperOpen",
     "doFan": "doFanStart", "bRunning": "bInWork", "iStep": "iStepNo",
     "tonWait": "tonStart", "diRun1": "diFb1", "diRun2": "diFb2",
     "doFan1": "doMainFan", "doFan2": "doResFan", "bFault1": "bErrMain",
     "bFault2": "bErrRes", "tonFb1": "tonChk1", "tonFb2": "tonChk2",
     "tonClear": "tonRelease",
     "rTFrostExh": "rTFrostExhaust", "rBypassSp": "rSpBypass", "aoBypass": "aoByp",
     "doBypass": "doByp", "bFrostRisk": "bFrostWarn", "rSpRotor": "rRotorTask",
     "aoRotor": "aoRotorSpd", "rCoolSp": "rSpCooling", "aoCoolValve": "aoValveCool",
     "doCoolPump": "doPumpCool", "diCondens": "diCondWater", "bUvDemand": "bUvReq",
     "doUvLamp": "doLampUv", "tonUvDelay": "tonUvWait", "bCmdPump": "bPumpCmd",
     "diPumpFb": "diFbPump", "diDryLevel": "diLevelDry", "tonPumpFb": "tonPumpChk",
     "rTRoom": "rRoomTemp", "rTRoomSp": "rSpRoom", "rKCascade": "rCascK",
     "rTSupplySp": "rSpSupplyT", "rDelta": "rDiff", "rHeatDemand": "rHeatReq",
     "doHeater1": "doHeatSt1", "doHeater2": "doHeatSt2", "doHeater3": "doHeatSt3",
     "rTSupply": "rSupplyTemp", "tonPurge": "tonBlow", "bCmdStop": "bStopCmd",
     "bStopped": "bIsStopped", "iStepStop": "iStopStepNo", "tonCoast": "tonRundown",
     "doHeaterEl": "doElHeat", "diOverheatSw": "diHotSw"},
    {"bCmdStart": "xCmdRun", "diRunFb": "xFbContactor", "bFireAlarm": "xPozharSignal",
     "bReset": "xRstBtn", "doStart": "qFanRun", "bFault": "xAlarm",
     "tonFb": "tmrFeedback", "rtReset": "trigRst", "bEnable": "xPermit",
     "rSpeedSp": "rTaskSpeed", "diVfdRun": "xInvRun", "aoSpeed": "qSpeedRef",
     "doRun": "qInvStart", "bFanRun": "xFanWork", "diDiffPress": "xDpSensor",
     "bClogged": "xFilterClog", "tonClog": "tmrClog", "rTWater": "rTwaterBack",
     "rValveSp": "rValveTask", "aoValve": "qValvePos", "doPump": "qPumpOn",
     "bFrostThreat": "xFrostDanger", "bCmdOpen": "xTaskOpen", "diOpenEnd": "xLimSwOpen",
     "doOpen": "qDamperDrv", "tonOpen": "tmrOpenChk", "bDemand": "xNeedHeat",
     "bAirFlow": "xAirOk", "diOverheat": "xOverheatSw", "doHeater": "qHeaterOn",
     "bOverheatFault": "xOverheatErr", "tonCool": "tmrCooldown", "bCmdRun": "xWorkCmd",
     "diDamperEnd": "xDampLimSw", "diFanRun": "xFanFback", "doDamper": "qDampCmd",
     "doFan": "qFanCmd", "bRunning": "xUnitWork", "iStep": "iStateStep",
     "tonWait": "tmrWaitFb", "diRun1": "xFbFan1", "diRun2": "xFbFan2",
     "doFan1": "qFanA", "doFan2": "qFanB", "bFault1": "xErrFanA",
     "bFault2": "xErrFanB", "tonFb1": "tmrFbA", "tonFb2": "tmrFbB",
     "tonClear": "tmrClean",
     "rTFrostExh": "rFrostTemp", "rBypassSp": "rBypTask", "aoBypass": "qBypassPos",
     "doBypass": "qBypassOn", "bFrostRisk": "xFrostRisk", "rSpRotor": "rTaskRotor",
     "aoRotor": "qRotorRef", "rCoolSp": "rTaskCool", "aoCoolValve": "qCoolPos",
     "doCoolPump": "qCoolPumpOn", "diCondens": "xCondSens", "bUvDemand": "xUvNeed",
     "doUvLamp": "qUvOn", "tonUvDelay": "tmrUvDly", "bCmdPump": "xPumpTask",
     "diPumpFb": "xPumpSens", "diDryLevel": "xDrySens", "tonPumpFb": "tmrPumpChk",
     "rTRoom": "rTempRoom", "rTRoomSp": "rTaskRoom", "rKCascade": "rKoef",
     "rTSupplySp": "rTaskSupply", "rDelta": "rErrVal", "rHeatDemand": "rNeedHeat",
     "doHeater1": "qTen1", "doHeater2": "qTen2", "doHeater3": "qTen3",
     "rTSupply": "rTempSupply", "tonPurge": "tmrPurge", "bCmdStop": "xStopTask",
     "bStopped": "xStopped", "iStepStop": "iStageStop", "tonCoast": "tmrCoast",
     "doHeaterEl": "qElTen", "diOverheatSw": "xOverheatSens"},
]


# ============================================================
# 5. Генерация + валидация
# ============================================================

def spec_variants(ru, flags):
    """Лёгкие вариации формулировки ТЗ (замены синонимичных оборотов)."""
    subs = [
        ("Разработать функциональный блок", "Реализовать функциональный блок"),
        ("Разработать программу", "Реализовать программу"),
        ("Требования:", "Условия работы:"),
    ]
    out = ru
    for (a, b), enabled in zip(subs, flags):
        if enabled:
            out = out.replace(a, b)
    return out


def expand_grid(grid):
    keys = sorted(grid.keys())
    for combo in itertools.product(*(grid[k] for k in keys)):
        yield dict(zip(keys, combo))


def _stable_flags(template_name, params, style_seed, count=4):
    """Return deterministic booleans without Python's randomized hash()."""

    material = json.dumps(
        {
            "template": template_name,
            "params": params,
            "style_seed": style_seed,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return tuple(bool(digest[index // 8] & (1 << (index % 8))) for index in range(count))


def generate_records(target_defects_per_reference=4):
    """Generate the complete synthetic corpus in a stable order."""

    records, log = [], []
    rid = 0
    for cat, tpls in TEMPLATES.items():
        for tpl, grid in tpls:
            for params in expand_grid(grid):
                for style_seed in range(2):  # 2 стилевые редакции эталона
                    flags = _stable_flags(tpl.__name__, params, style_seed)
                    name, decls, nodes, ru = tpl(params)
                    base_style = Style(latch_two_if=(style_seed == 1 and flags[0]))
                    kind = "PROGRAM" if name.startswith("PRG") else "FUNCTION_BLOCK"
                    code = render_unit(kind, name, decls, nodes, base_style)

                    # --- валидация эталона ---
                    try:
                        seq = canonize(code, strict=True)
                    except Exception as e:  # noqa: BLE001
                        log.append(f"[FAIL-PARSE] {name} {params}: {e}")
                        continue
                    raw_share = sum(1 for s in seq if s.startswith("B_RAW")) / max(1, len(seq))
                    if raw_share > 0.05:
                        log.append(f"[FAIL-RAW] {name} {params}: raw={raw_share:.0%}")
                        continue

                    # --- эквивалент: согласованное переименование + стиль ---
                    scheme = RENAME_SCHEMES[rid % len(RENAME_SCHEMES)]
                    eq_style = Style(latch_two_if=not base_style.latch_two_if
                                     if any(isinstance(n, Latch) for n in nodes) else False,
                                     rename=scheme, zero="0.0")
                    eq_code = render_unit(kind, name, decls, nodes, eq_style)
                    try:
                        eq_seq = canonize(eq_code, strict=True)
                    except Exception as e:  # noqa: BLE001
                        log.append(f"[FAIL-EQ-PARSE] {name}: {e}")
                        continue
                    d_eq = block_levenshtein(seq, eq_seq)
                    if d_eq != 0:
                        log.append(f"[WARN-EQ] {name} {params}: d_eq={d_eq} (ожидалось 0)")
                        # не отбрасываем: фиксируем как честный негативный кейс
                    # --- дефекты по таксономии ---
                    defects = []
                    for dcode, dclass, fn in MUTATIONS:
                        mut, descr = fn(nodes)
                        if mut is None:
                            continue
                        bug_code = render_unit(kind, name, decls, mut, base_style)
                        try:
                            bug_seq = canonize(bug_code, strict=True)
                        except Exception:  # noqa: BLE001
                            continue
                        d_bug = block_levenshtein(seq, bug_seq)
                        defects.append({
                            "taxonomy": dcode, "class": dclass, "description": descr,
                            "st_code": bug_code, "block_distance": d_bug,
                            "block_seq": bug_seq,
                            "norm_distance": round(block_distance_norm(seq, bug_seq), 4),
                            "detected": d_bug >= 1,
                        })
                        if len(defects) >= target_defects_per_reference:
                            break

                    rid += 1
                    records.append({
                        "id": f"SYN-{rid:04d}",
                        "category": cat,
                        "template": tpl.__name__,
                        "unit_name": name,
                        "params": params,
                        "ru_spec": spec_variants(ru, flags[1:]),
                        "st_code": code,
                        "block_seq": seq,
                        "equiv": {
                            "st_code": eq_code,
                            "block_seq": eq_seq,
                            "block_distance": d_eq,
                        },
                        "defects": defects,
                    })
    return records, log


def records_jsonl(records):
    """Serialize records canonically for hashing and optional export."""

    return "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    )


def select_working_subset(records, maximum_per_structure=2):
    """Reproduce the Article 4 synthetic working-subset selection."""

    seen = {}
    selected = []
    for record in records:
        key = (record["template"], tuple(record["block_seq"]))
        seen[key] = seen.get(key, 0)
        if seen[key] >= maximum_per_structure:
            continue
        seen[key] += 1
        selected.append(record)
    return selected


def _sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def summarize_records(records, generation_log=()):
    """Return deterministic aggregate evidence for the public manifest."""

    category_counts = {}
    taxonomy_counts = {}
    for record in records:
        category = record["category"]
        category_counts[category] = category_counts.get(category, 0) + 1
        for defect in record["defects"]:
            bucket = taxonomy_counts.setdefault(
                defect["taxonomy"],
                {"defect_variants": 0, "nonzero_distance": 0},
            )
            bucket["defect_variants"] += 1
            bucket["nonzero_distance"] += int(defect["detected"])

    selected = select_working_subset(records)
    selected_results = []
    kind_counts = {}
    matches = 0
    for record in selected:
        for defect in record["defects"]:
            result = edit_script(
                record["block_seq"],
                defect["block_seq"],
                tie_break=DEFAULT_TIE_BREAK,
            )
            selected_results.append(result)
            matched = matches_expected_edit_kind(result, defect["taxonomy"])
            matches += int(matched)
            bucket = kind_counts.setdefault(
                defect["taxonomy"],
                {"defect_variants": 0, "declared_edit_kind_matches": 0},
            )
            bucket["defect_variants"] += 1
            bucket["declared_edit_kind_matches"] += int(matched)

    defect_count = sum(len(record["defects"]) for record in records)
    detected_count = sum(
        int(defect["detected"])
        for record in records
        for defect in record["defects"]
    )
    defects_per_reference = {}
    for record in records:
        count = str(len(record["defects"]))
        defects_per_reference[count] = defects_per_reference.get(count, 0) + 1
    raw_symbols = sum(
        symbol.startswith("B_RAW")
        for record in records
        for sequence in (
            record["block_seq"],
            record["equiv"]["block_seq"],
            *(defect["block_seq"] for defect in record["defects"]),
        )
        for symbol in sequence
    )
    represented_taxonomy = set(taxonomy_counts)
    return {
        "schema_version": "article4-summary/v1",
        "generator_version": GENERATOR_VERSION,
        "scope": "synthetic_only",
        "protocol": {
            "record_schema_version": SCHEMA_VERSION,
            "strict_canonicalization": True,
            "maximum_defects_per_reference": max(
                (len(record["defects"]) for record in records),
                default=0,
            ),
            "configured_mutation_types": len(MUTATIONS),
            "represented_mutation_types": len(represented_taxonomy),
            "configured_but_not_represented": [
                code for code, _defect_class, _function in MUTATIONS
                if code not in represented_taxonomy
            ],
        },
        "full_corpus": {
            "reference_records": len(records),
            "category_counts": dict(sorted(category_counts.items())),
            "template_count": len({record["template"] for record in records}),
            "unique_block_sequences": len({
                tuple(record["block_seq"]) for record in records
            }),
            "template_sequence_strata": len({
                (record["template"], tuple(record["block_seq"]))
                for record in records
            }),
            "equivalent_distance_zero": sum(
                record["equiv"]["block_distance"] == 0 for record in records
            ),
            "defect_variants": defect_count,
            "defects_with_nonzero_distance": detected_count,
            "comparisons": len(records) + defect_count,
            "rendered_program_texts": len(records) * 2 + defect_count,
            "raw_symbols": raw_symbols,
            "references_by_defect_count": dict(sorted(defects_per_reference.items())),
            "taxonomy": dict(sorted(taxonomy_counts.items())),
            "jsonl_sha256": _sha256_text(records_jsonl(records)),
        },
        "working_subset": {
            "selection_rule": "first-two-per-template-and-canonical-sequence/v1",
            "reference_records": len(selected),
            "defect_variants": len(selected_results),
            "declared_edit_kind_matches": matches,
            "tie_break_id": DEFAULT_TIE_BREAK.value,
            "taxonomy": dict(sorted(kind_counts.items())),
            "edit_profile": build_error_profile(selected_results),
            "jsonl_sha256": _sha256_text(records_jsonl(selected)),
        },
        "generation_log_entries": len(tuple(generation_log)),
    }


def write_jsonl(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(records_jsonl(records), encoding="utf-8", newline="\n")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="optional path for the generated synthetic JSONL",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        help="optional path for the deterministic JSON summary",
    )
    parser.add_argument(
        "--target-defects-per-reference",
        type=int,
        default=4,
    )
    args = parser.parse_args(argv)

    records, log = generate_records(args.target_defects_per_reference)
    summary = summarize_records(records, log)
    if args.output is not None:
        write_jsonl(args.output, records)
    rendered_summary = json.dumps(
        summary,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    if args.summary is not None:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(rendered_summary, encoding="utf-8", newline="\n")
    print(rendered_summary, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
