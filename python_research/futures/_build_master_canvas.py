# -*- coding: utf-8 -*-
import json
from datetime import datetime, timezone
from pathlib import Path

lab = Path(r"C:\Users\123\.cursor\skills\bybit-top25-scan\strategy_tester_repo\python_research\futures")
report = json.loads((lab / "_pitbull_master_audit.json").read_text(encoding="utf-8"))["report"]
curves = json.loads((lab / "_pitbull_master_curves.json").read_text(encoding="utf-8"))

order = ["V0_rules", "V1_sized", "V2_hour1", "V3_dual", "V4_nn"]
names = {
    "V0_rules": "Старые правила",
    "V1_sized": "Потолок 1.15",
    "V2_hour1": "Реакция 1-го часа",
    "V3_dual": "Раннер и спринт",
    "V4_nn": "Сеть поверх правил",
}
keys = ["2022", "2023", "2024", "2025", "2026H1", "2026Q"]
cats = ["2022", "2023", "2024", "2025", "2026 до июля", "Квартал"]

base = curves["V1_sized"]["full"]
n = 28
idxs = []
for i in range(n):
    k = round(i * (len(base) - 1) / (n - 1))
    if not idxs or k != idxs[-1]:
        idxs.append(k)
times = [base[i][0] for i in idxs]


def at(series, ts):
    val = series[0][1]
    for t, v in series:
        if t > ts:
            break
        val = v
    return round(val)


def line(key, tone=None):
    data = ", ".join(str(at(curves[key]["full"], t)) for t in times)
    bit = ', tone: "%s"' % tone if tone else ""
    return '{ name: "%s", data: [%s]%s }' % (names[key], data, bit)


def bars(field, scale=1):
    lines = []
    tones = {"V1_sized": "info", "V4_nn": "success", "V3_dual": "danger"}
    for key in order:
        data = ", ".join(str(round(report[key][k][field] * scale, 3)) for k in keys)
        bit = ', tone: "%s"' % tones[key] if key in tones else ""
        lines.append('{ name: "%s", data: [%s]%s }' % (names[key], data, bit))
    return ",\n          ".join(lines)


labels = [datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%y-%m") for t in times]
v1 = report["V1_sized"]["full"]
v4 = report["V4_nn"]["full"]
tsx = """import { BarChart, Divider, Grid, H1, H2, LineChart, Stack, Stat, Text } from "cursor/canvas";

const dates = [%s];
const years = [%s];

export default function PitbullMasterCompare() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>Пять вариантов на одном стенде</H1>
        <Text tone="secondary">
          Один и тот же список сигналов, одна очередь из двух слотов и одна комиссия на всех. Реакция первого часа и разделение на раннера со спринтом проигрывают удержанию. Сеть, которой разрешено ничего не делать, чуть поднимает путь и не режет ни 2025-й, ни квартал.
        </Text>
      </Stack>
      <Grid columns={3} gap={16}>
        <Stat value="$%s" label="Потолок 1.15, весь путь" />
        <Stat value="$%s" label="Сеть поверх правил, весь путь" tone="success" />
        <Stat value="65%% и 65%%" label="Максимальная просадка, правила и сеть" />
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
        ]}
      />
      <Text tone="tertiary" size="small">
        Ось времени: месяц точки с 2022 по ноябрь 2026. Раннер со спринтом заканчивает около нуля и на этот график не помещён, его столбцы ниже. Сеть идёт выше потолка 1.15 после 2023-го.
      </Text>
      <H2>Доходность свежего старта, процент</H2>
      <BarChart
        categories={years}
        height={260}
        valueSuffix="%%"
        series={[
          %s
        ]}
      />
      <Text tone="tertiary" size="small">
        Каждый столбец начинается с 2 000 долларов. Сеть совпадает с потолком 1.15 в 2022, 2025 и квартале. В 2024-м −13.5%% вместо −15.4%%. Реакция первого часа ломает первую половину 2026-го.
      </Text>
      <H2>Максимальная просадка свежего старта, процент</H2>
      <BarChart
        categories={years}
        height={220}
        valueSuffix="%%"
        series={[
          %s
        ]}
      />
      <H2>Число сделок свежего старта</H2>
      <BarChart
        categories={years}
        height={220}
        series={[
          %s
        ]}
      />
      <H2>Доля пяти лучших в плюсе, процент</H2>
      <BarChart
        categories={years}
        height={220}
        valueSuffix="%%"
        series={[
          %s
        ]}
      />
      <Text tone="tertiary" size="small">
        Источник: локальный часовой кэш Bybit, комиссия 0.11%% и 3 б.п. проскальзывания на каждом варианте одинаково. На всём пути доля пяти сделок 55.7%% у потолка и 56.0%% у сети, просадка обоих 65%%. Сеть поднимает финиш с $64 004 до $69 436, но не выполняет условие снизить и просадку, и зависимость от пяти сделок. В боте остаётся потолок 1.15.
      </Text>
    </Stack>
  );
}
""" % (
    ", ".join('"%s"' % x for x in labels),
    ", ".join('"%s"' % x for x in cats),
    format(v1["final"], ",.0f").replace(",", " "),
    format(v4["final"], ",.0f").replace(",", " "),
    line("V1_sized", "info"),
    line("V4_nn", "success"),
    line("V2_hour1", "warning"),
    bars("ret"),
    bars("dd"),
    bars("n"),
    bars("top5", 100),
)
out = Path(r"C:\Users\123\.cursor\projects\c-Users-123-cursor-skills-bybit-top25-scan\canvases\pitbull-master-compare.canvas.tsx")
out.write_text(tsx.replace("%%", "%"), encoding="utf-8", newline="\n")
print(out)
