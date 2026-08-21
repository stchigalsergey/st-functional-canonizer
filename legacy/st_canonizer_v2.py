# -*- coding: utf-8 -*-
"""
st_canonizer_v2 — канонизатор с улучшенным разбором CASE (roadmap README:
«улучшение разбора CASE»). Оригинальный st_canonizer.py НЕ изменён — v2
наследует его парсер и переопределяет только parse_case.

Проблема v1: после первого кейса метки `10:`, `20:` … не распознавались и
уходили в B_RAW (см. статью 2, табл. 2 — B_RAW сосредоточены в CASE-автоматах).

Решение v2: тело кейса разбирается до тех пор, пока впереди не обнаружится
НОВАЯ МЕТКА — последовательность токенов NUMBER/ID/DOT/COMMA/минус,
завершающаяся COLON (двоеточием, а не `:=`). Лексер различает COLON и ASSIGN,
поэтому lookahead однозначен.

Добавлено 06.07.2026 (автономная сессия): используется генератором
синтетического корпуса и внешней валидацией; сравнение v1/v2 на реальном
корпусе — см. _v2_compare_report.txt.
"""

from st_canonizer import (  # noqa: F401  (реэкспорт публичного API)
    Parser, Case, Raw, st_lex, split_unit, emit_blocks,
    block_levenshtein, block_distance_norm,
)


class ParserV2(Parser):
    _LABEL_INNER = ("NUMBER", "ID", "DOT", "COMMA")

    def _case_label_ahead(self) -> bool:
        """True, если с текущей позиции идёт метка CASE: (NUM|ID[.ID]|,|-)+ ':'"""
        j = self.i
        seen_core = False
        while j < len(self.t):
            tk = self.t[j]
            if tk.kind in self._LABEL_INNER or (tk.kind == "OP" and tk.val == "-"):
                if tk.kind in ("NUMBER", "ID"):
                    seen_core = True
                j += 1
                continue
            return seen_core and tk.kind == "COLON"
        return False

    def _parse_case_body(self):
        """Как parse_stmt_list({'END_CASE','ELSE'}), но останавливается перед
        следующей меткой кейса (lookahead)."""
        body = []
        while True:
            tk = self.peek()
            if tk is None:
                break
            if tk.kind == "KW" and tk.val in ("END_CASE", "ELSE"):
                break
            if self._case_label_ahead():
                break
            if tk.kind == "SEMI":
                self.eat()
                continue
            start = self.i
            try:
                st = self.parse_stmt()
                if st is not None:
                    body.append(st)
            except SyntaxError:
                self.i = start
                raw = self._skip_to_semi()
                if raw:
                    body.append(Raw(raw))
        return body

    def parse_case(self) -> Case:
        self.expect("KW", "CASE")
        sel = self.parse_expr()
        self.expect("KW", "OF")
        cases = []
        els = None
        while not self.at_kw("END_CASE") and not self.at_kw("ELSE"):
            label_parts = []
            while not self.at("COLON") and self.peek() is not None \
                    and not self.at_kw("END_CASE", "ELSE"):
                label_parts.append(self.eat().val)
            if self.at("COLON"):
                self.eat()
            b = self._parse_case_body()
            cases.append((" ".join(label_parts), b))
            if self.at_kw("END_CASE", "ELSE"):
                break
        if self.at_kw("ELSE"):
            self.eat()
            els = self.parse_stmt_list({"END_CASE"})
        self.expect("KW", "END_CASE")
        return Case(sel, cases, els)


def canonize(src: str):
    """ST-исходник -> последовательность блок-символов (парсер v2)."""
    vars_map, body = split_unit(src)

    def type_of(name):
        return vars_map.get(name.lower(), (None, None))[1]

    def role_of(name):
        return vars_map.get(name.lower(), (None, None))[0]

    ctx = {"type_of": type_of, "role_of": role_of, "vars": vars_map}
    toks = st_lex(body)
    stmts = ParserV2(toks).parse_stmt_list()
    return emit_blocks(stmts, ctx)


if __name__ == "__main__":
    demo = """
PROGRAM P
VAR iStep : INT; bRun : BOOL; END_VAR
VAR_OUTPUT doFan : BOOL; END_VAR
CASE iStep OF
    0:
        doFan := FALSE;
        IF bRun THEN iStep := 10; END_IF
    10:
        doFan := TRUE;
        IF NOT bRun THEN iStep := 0; END_IF
END_CASE
END_PROGRAM
"""
    seq = canonize(demo)
    print(seq)
    assert not any(s.startswith("B_RAW") for s in seq), "v2 must parse all labels"
    print("OK: CASE labels parsed without B_RAW")
