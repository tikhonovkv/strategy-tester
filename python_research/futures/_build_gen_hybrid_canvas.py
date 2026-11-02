# -*- coding: utf-8 -*-
import json
from datetime import datetime, timezone
from pathlib import Path

lab = Path(r"C:\Users\123\.cursor\skills\bybit-top25-scan\strategy_tester_repo\python_research\futures")
report = json.loads((lab / "_gen_and_hybrid_audit.json").read_text(encoding="utf-8"))["report"]
curves = json.loads((lab / "_gen_and_hybrid_curves.json").read_text(encoding="utf-8"))

order = [
    ("V1_base", "Эталон V1 (база)", "info"),
    ("G1b_asym_top2_tie", "Вариант 2: Асимметрия + Tie-break", "success"),
    ("H_sleeve48_split", "Вариант 3А: Сплит 70/30 с Carry/Mom", "warning"),
    ("H_sleeve48_idle", "Вариант 2+3Б: Асимметрия + Idle Carry", "neutral"),
    ("G2_coil_7alts", "Триггер 24h пробоя без отката", "danger"),
]
keys = ["2022", "2023", "2024", "2025", "2026H1", "2026Q"]
cats = ["2022", "2023", "2024", "2025", "2026 до июля", "Квартал"]

base_c = curves["V1_base"]["full"]
n = 28
idxs = []
for i in range(n):
    k = round(i * (len(base_c) - 1) / (n - 1))
    if not idxs or k != idxs[-1]:
        idxs.append(k)
times = [base_c[i][0] for i in idxs]


def at(series, ts):
    val = series[0][1]
    for t, v in series:
        if t > ts:
            break
        val = v
    return round(val)


def line(key, title, tone=None):
    data = ", ".join(str(at(curves[key]["full"], t)) for t in times)
    bit = ', tone: "%s"' % tone if tone else ""
    return '{ name: "%s", data: [%s]%s }' % (title, data, bit)


def bars(field, scale=1.0):
    lines = []
    for key, title, tone in order:
        data = ", ".join(str(round(report[key][k][field] * scale, 1)) for k in keys)
        bit = ', tone: "%s"' % tone if tone else ""
        lines.append('{ name: "%s", data: [%s]%s }' % (title, data, bit))
    return ",\n          ".join(lines)


labels = [datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%y-%m") for t in times]

table_rows = []
for key, title, _tone in [
    ("V1_base", "Эталон V1 (потолок 1.15)", "info"),
    ("G1_asym_only", "Вариант 2А: Асимметрия LONG (ETH/BTC/Топ-2 RS)", "success"),
    ("G1_tie_only", "Вариант 2Б: Tie-break слотов по объёму", "neutral"),
    ("G1b_asym_top2_tie", "Вариант 2 (Полный): Асимметрия + Tie-break", "success"),
    ("H_sleeve48_split", "Вариант 3А: Сплит 70% Шварц + 30% Carry/Mom 48ч", "warning"),
    ("H_sleeve48_idle", "Вариант 2+3Б: Асимметрия + Свободная маржа в Carry/Mom", "success"),
    ("G2_coil_7alts", "Триггер 24h пробоя сжатия (без отката к EMA48)", "danger"),
    ("G3_majors25_reclaim", "Расширение на 25 монет без фильтра ядра", "danger"),
]:
    r = report[key]
    f = r["full"]
    cells = [
        title,
        f"{r['2022']['ret']:+.1f}%",
        f"{r['2023']['ret']:+.1f}%",
        f"{r['2024']['ret']:+.1f}%",
        f"{r['2025']['ret']:+.1f}%",
        f"{r['2026H1']['ret']:+.1f}%",
        f"{r['2026Q']['ret']:+.1f}%",
        "$" + format(f["final"], ",.0f").replace(",", " "),
        f"{f['dd']:.1f}%",
        f"{f['top5']*100:.1f}%",
    ]
    table_rows.append("[" + ", ".join('"%s"' % c for c in cells) + "]")

tsx = """import { BarChart, Callout, Divider, Grid, H1, H2, LineChart, Stack, Stat, Table, Text } from "cursor/canvas";

const dates = [%s];
const years = [%s];

export default function GeneratorHybridResults() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>Вариант 2 (Генератор и Асимметрия) + Вариант 3 (Гибрид с Carry/Momentum)</H1>
        <Text tone="secondary">
          Честное сравнение с 2022 по ноябрь 2026 (старт $2 000, комиссия 0.11%% + 3 б.п. проскальзывания на каждую сделку). Проверены оба направления: перестройка отбора входов (асимметрия сторон + приоритет объёма) и подключение второго рукава Carry/Momentum 48ч.
        </Text>
      </Stack>

      <Callout tone="success" title="Два главных прорыва">
        1) Внутри самого Шварца 7x: разрешение SHORT по всем 7 альтам, а LONG только для ETH/BTC и топ-2 лидеров 24ч импульса + выбор слота по всплеску объёма удваивает капитал ($130 362 против $68 313), снижает просадку с 65.8%% до 59.4%% и улучшает КАЖДЫЙ из 6 периодов без исключения.
        2) Подключение рукава Carry/Momentum (ребаланс раз в 48ч с учётом только дельты оборота) на свободную маржу вытаскивает 2024 год из −15.4%% в +11.1%% и снижает долю топ-5 сделок до 50.0%%.
      </Callout>

      <Grid columns={4} gap={14}>
        <Stat value="$68 313" label="Эталон V1 (DD 65.8%%)" tone="info" />
        <Stat value="$130 362" label="Вариант 2: Асимметрия + Объём (DD 59.4%%)" tone="success" />
        <Stat value="+11.1%%" label="2024 год в гибриде (было −15.4%%)" tone="success" />
        <Stat value="52.1%% / 50.0%%" label="DD в сплите / Доля топ-5 в гибриде" tone="warning" />
      </Grid>

      <Divider />

      <H2>Капитал непрерывного счёта, доллары</H2>
      <LineChart
        categories={dates}
        height={280}
        valuePrefix="$"
        fill={false}
        series={[
          %s,
          %s,
          %s,
          %s,
        ]}
      />
      <Text tone="tertiary" size="small">
        Ось времени: 2022 — ноябрь 2026. Вариант 2 (Асимметрия лонгов + tie-break по объёму) обгоняет эталон на всём пути, а гибрид со вторым рукавом на свободной марже добавляет доход в боковые фазы, когда слоты Шварца пустуют.
      </Text>

      <H2>Доходность каждого периода со свежего старта $2 000, %%</H2>
      <BarChart
        categories={years}
        height={260}
        valueSuffix="%%"
        series={[
          %s
        ]}
      />
      <Text tone="tertiary" size="small">
        Обратите внимание на 2024 год: чистый пробойный генератор даёт −14.8%%, но добавление рукава Carry/Momentum 48ч переводит 2024 год в +4.2%% (в сплите 70/30) и +11.1%% (на свободной марже), не жертвуя 2025 годом (+258.4%%) и кварталом 2026Q (+290.4%%).
      </Text>

      <H2>Максимальная просадка по периодам, %%</H2>
      <BarChart
        categories={years}
        height={220}
        valueSuffix="%%"
        series={[
          %s
        ]}
      />

      <H2>Полная таблица всех проверенных конфигураций Варианта 2 и Варианта 3</H2>
      <Table
        headers={["Конфигурация", "2022", "2023", "2024", "2025", "2026H1", "2026Q", "Весь путь $", "Max DD", "Топ-5 доля"]}
        columnAlign={["left", "right", "right", "right", "right", "right", "right", "right", "right", "right"]}
        striped
        rows={[
          %s
        ]}
      />
      <Text tone="tertiary" size="small">
        Источник: _gen_and_hybrid_audit.py. Почему провалились G2 (пробой 24h без отката) и G3 (все 25 монет подряд): вход на пробое 24ч максимума добавляет ложные входы на вершине свечи, а мелкие альты из топ-25 при плече 7x дают слишком много ложных пересечений EMA48. Зато правило асимметрии сторон + выбор слота по всплеску объёма прошло все 6 периодов и внесено в боевой код.
      </Text>
    </Stack>
  );
}
""" % (
    ", ".join('"%s"' % x for x in labels),
    ", ".join('"%s"' % x for x in cats),
    line("V1_base", "Эталон V1", "info"),
    line("G1b_asym_top2_tie", "Вариант 2: Асимметрия + Объём", "success"),
    line("H_sleeve48_split", "Вариант 3А: Сплит 70/30", "warning"),
    line("H_sleeve48_idle", "Вариант 2+3Б: Гибрид на свободной марже"),
    bars("ret"),
    bars("dd"),
    ",\n          ".join(table_rows),
)

out = Path(r"C:\Users\123\.cursor\projects\c-Users-123-cursor-skills-bybit-top25-scan\canvases\generator-hybrid-results.canvas.tsx")
out.write_text(tsx.replace("%%", "%"), encoding="utf-8", newline="\n")
print("wrote", out)
