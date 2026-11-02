# -*- coding: utf-8 -*-
import json
from datetime import datetime, timezone
from pathlib import Path

base = Path(r"C:\Users\123\.cursor\skills\bybit-top25-scan\strategy_tester_repo\python_research\futures")
curves = json.loads((base / "_stabilize_curves.json").read_text(encoding="utf-8"))
report = json.loads((base / "_stabilize_audit.json").read_text(encoding="utf-8"))["report"]

order = ["rules", "no_boost", "vol_half", "ratchet", "partial10", "all4"]
names = {
    "rules": "Правила",
    "no_boost": "Потолок серии 1.15",
    "vol_half": "Полразмера ATR",
    "ratchet": "Ступень капитала",
    "partial10": "Фиксация 10%",
    "all4": "Все четыре",
}
tones = {
    "rules": "info",
    "no_boost": "success",
    "vol_half": "warning",
    "ratchet": "neutral",
    "partial10": "danger",
    "all4": "neutral",
}

rules_ts = [p[0] for p in curves["rules"]["curve"]]
n = 28
idxs = []
for i in range(n):
    idx = round(i * (len(rules_ts) - 1) / (n - 1))
    if not idxs or idx != idxs[-1]:
        idxs.append(idx)
times = [rules_ts[i] for i in idxs]


def at(series, ts):
    val = series[0][1]
    for t, v in series:
        if t > ts:
            break
        val = v
    return val


labels = [datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%y-%m") for t in times]
equity = {k: [round(at(curves[k]["curve"], t)) for t in times] for k in order}
water = {k: [round(at(curves[k]["underwater"], t), 1) for t in times] for k in order}

periods = ["2022", "2023", "2024", "2025", "2026H1", "2026Q"]
period_labels = ["2022", "2023", "2024", "2025", "2026 до июля", "Квартал"]
rets = {k: [report[k][p]["ret"] for p in periods] for k in order}
dds = {k: [report[k][p]["dd"] for p in periods] for k in order}


def series(mapping, as_int=False):
    lines = []
    for k in order:
        data = mapping[k]
        body = ", ".join(str(int(v) if as_int else v) for v in data)
        lines.append(
            '          { name: "%s", data: [%s], tone: "%s" },'
            % (names[k], body, tones[k])
        )
    return "\n".join(lines)


def finals(key):
    row = report[key]["full"]
    return row["final"], row["dd"], row["calmar"], row["ret"]


rf, rd, rc, rr = finals("rules")
nf, nd, nc, nr = finals("no_boost")

tsx = """import { BarChart, Divider, Grid, H1, H2, LineChart, Stack, Stat, Text } from "cursor/canvas";

const dates = [%s];

export default function StabilizeDrawdown() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>Стабилизация Шварца: что выдержало стенд</H1>
        <Text tone="secondary">
          Один и тот же счёт с января 2022 по ноябрь 2026, старт 2 000 долларов. В бота переносится только потолок серии побед 1.15 вместе с уже существующим срезом после убытков. Остальные три контура на стенде проиграли отдельным годам или текущему кварталу.
        </Text>
      </Stack>
      <Grid columns={3} gap={16}>
        <Stat value="$%s" label="Правила, финиш всего пути" />
        <Stat value="$%s" label="Потолок 1.15, финиш всего пути" tone="success" />
        <Stat value="%s%% против %s%%" label="Максимальная просадка, правила и потолок" />
      </Grid>
      <Divider />
      <H2>Капитал непрерывного счёта, доллары</H2>
      <LineChart
        categories={dates}
        height={280}
        valuePrefix="$"
        fill={false}
        series={[
%s
        ]}
      />
      <Text tone="tertiary" size="small">
        Ось времени: месяц точки. Линии — капитал после каждой сделки, прореженный до %d точек. Ступень капитала и связка всех четырёх контуров заканчивают ниже правил.
      </Text>
      <H2>Просадка от пика, процент</H2>
      <LineChart
        categories={dates}
        height={240}
        valueSuffix="%%"
        fill={false}
        series={[
%s
        ]}
      />
      <Text tone="tertiary" size="small">
        Ноль — новый максимум счёта. Ниже нуля — сколько процентов уже отдано от этого максимума. Это один непрерывный счёт, не свежий старт года.
      </Text>
      <H2>Доходность свежего старта, процент</H2>
      <BarChart
        categories={%s}
        height={240}
        valueSuffix="%%"
        series={[
%s
        ]}
      />
      <Text tone="tertiary" size="small">
        Каждый столбец — отдельный счёт с 2 000 долларов. Полразмера ATR поднимает весь путь, но режет 2022. Фиксация 10%% режет 2022, первую половину 2026 и квартал.
      </Text>
      <H2>Максимальная просадка свежего старта, процент</H2>
      <BarChart
        categories={%s}
        height={240}
        valueSuffix="%%"
        series={[
%s
        ]}
      />
      <Text tone="tertiary" size="small">
        Источник: локальный часовой кэш Bybit, выход по часовому закрытию, комиссия 0.11%% и проскальзывание 3 б.п. Стенд _stabilize_audit.py. Потолок 1.15 не ухудшил ни один период: весь путь %s%% против %s%% у правил, просадка %s%% против %s%%.
      </Text>
    </Stack>
  );
}
""" % (
    ", ".join('"%s"' % x for x in labels),
    format(rf, ",.0f").replace(",", " "),
    format(nf, ",.0f").replace(",", " "),
    rd,
    nd,
    series(equity, as_int=True),
    len(times),
    series(water),
    json.dumps(period_labels, ensure_ascii=False),
    series(rets),
    json.dumps(period_labels, ensure_ascii=False),
    series(dds),
    nr,
    rr,
    nd,
    rd,
)

out = Path(r"C:\Users\123\.cursor\projects\c-Users-123-cursor-skills-bybit-top25-scan\canvases\stabilize-drawdown.canvas.tsx")
out.write_text(tsx, encoding="utf-8", newline="\n")
print(out)
print("points", len(times), "labels", labels[0], labels[-1])
