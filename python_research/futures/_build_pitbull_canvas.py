# -*- coding: utf-8 -*-
import json
from datetime import datetime, timezone
from pathlib import Path

lab = Path(r"C:\Users\123\.cursor\skills\bybit-top25-scan\strategy_tester_repo\python_research\futures")
curves = json.loads((lab / "_pitbull_curves.json").read_text(encoding="utf-8"))
sprint = json.loads((lab / "_sprint_curves.json").read_text(encoding="utf-8"))
nn = json.loads((lab / "_pitbull_reaction_nn.json").read_text(encoding="utf-8"))["report"]
sp = json.loads((lab / "_sprint_reentry.json").read_text(encoding="utf-8"))["report"]

rules_c = curves["rules"]
nn_c = curves["nn"]
s3_c = sprint["sprint3"]["full"]
base_ts = [p[0] for p in rules_c]
n = 28
idxs = []
for i in range(n):
    idx = round(i * (len(base_ts) - 1) / (n - 1))
    if not idxs or idx != idxs[-1]:
        idxs.append(idx)
times = [base_ts[i] for i in idxs]


def at(series, ts):
    val = series[0][1]
    for t, v in series:
        if t > ts:
            break
        val = v
    return round(v if False else val)


def aligned(series):
    return [round(at(series, t)) for t in times]


labels = [datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%y-%m") for t in times]
cats = ["2022", "2023", "2024", "2025", "2026 до июля", "Квартал"]
keys = ["2022", "2023", "2024", "2025", "2026H1", "2026Q"]


def col(block, field):
    return [block[k][field] for k in keys]


rules_ret = col(sp["rules"], "ret")
cut_ret = col(sp["cut3"], "ret")
s3_ret = col(sp["sprint3"], "ret")
s4_ret = col(sp["sprint4"], "ret")
nn_ret = [rules_ret[0]] + [nn["nn"][k]["ret"] for k in keys[1:]]
rules_dd = col(sp["rules"], "dd")
nn_dd = [rules_dd[0]] + [nn["nn"][k]["dd"] for k in keys[1:]]
s3_dd = col(sp["sprint3"], "dd")
rules_top = [round(sp["rules"][k]["top5"] * 100, 1) for k in keys]
nn_top = [rules_top[0]] + [round(nn["nn"][k]["top5"] * 100, 1) for k in keys[1:]]


def ser(name, data, tone=None):
    tone_bit = ', tone: "%s"' % tone if tone else ""
    return '{ name: "%s", data: [%s]%s }' % (name, ", ".join(str(v) for v in data), tone_bit)


tsx = """import { BarChart, Divider, Grid, H1, H2, LineChart, Stack, Stat, Text } from "cursor/canvas";

const dates = [%s];
const years = [%s];

export default function PitbullSprintNn() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>Спринты и сеть реакций против удержания</H1>
        <Text tone="secondary">
          Одни и те же сигналы Шварца, старт 2 000 долларов. Короткий спринт на 3–4 ATR с перезаходом дробит крупные ходы, но непрерывный счёт этого не переживает. Сеть, которую штрафовали за ранний выход из живого тренда, оставляет удержание и подрезает только слабое начало.
        </Text>
      </Stack>
      <Grid columns={3} gap={16}>
        <Stat value="$64 004" label="Правила, весь путь" />
        <Stat value="$72 230" label="Сеть реакций, весь путь" tone="success" />
        <Stat value="$870" label="Спринт 3 ATR с перезаходом" tone="danger" />
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
        Ось времени: месяц точки с 2022 по ноябрь 2026. Спринт на 3 ATR сходит к нулю уже в 2022-м. Сеть и правила идут рядом; расхождение копится после 2024-го.
      </Text>
      <H2>Доходность свежего старта, процент</H2>
      <BarChart
        categories={years}
        height={240}
        valueSuffix="%%"
        series={[
          %s,
          %s,
          %s,
        ]}
      />
      <Text tone="tertiary" size="small">
        Каждый столбец начинается с 2 000 долларов. В 2022 у сети ещё нет прошлого для обучения, поэтому столбец совпадает с правилами. Сеть вытаскивает 2024-й из −15%% в +12%% и чуть поднимает первую половину 2026-го. За это она отдаёт 2025-й: +114%% вместо +156%%, и квартал +172%% вместо +179%%.
      </Text>
      <H2>Доля пяти лучших в плюсе года, процент</H2>
      <BarChart
        categories={years}
        height={220}
        valueSuffix="%%"
        series={[
          %s,
          %s,
        ]}
      />
      <Text tone="tertiary" size="small">
        В 2025-м сеть снижает долю пяти сделок с 78%% до 48%%. Это тот же год, где прибыль стала меньше. На всём пути доля остаётся 55%% против 56%% у правил: зависимость от немногих больших ходов никуда не делась.
      </Text>
      <H2>Максимальная просадка свежего старта, процент</H2>
      <BarChart
        categories={years}
        height={220}
        valueSuffix="%%"
        series={[
          %s,
          %s,
          %s,
        ]}
      />
      <Text tone="tertiary" size="small">
        Источник: локальный часовой кэш Bybit, комиссия 0.11%% и 3 б.п. проскальзывания на каждый вход и выход. Порог сети выбирался только по предыдущему году. В 2024-м сеть дала плюс, но просадка выросла с 24%% до 42%%. Боевой бот не менялся: квартал и 2025-й у сети хуже правил.
      </Text>
    </Stack>
  );
}
""" % (
    ", ".join('"%s"' % x for x in labels),
    ", ".join('"%s"' % x for x in cats),
    ser("Правила", aligned(rules_c), "info"),
    ser("Сеть реакций", aligned(nn_c), "success"),
    ser("Спринт 3 ATR", aligned(s3_c), "danger"),
    ser("Правила", rules_ret, "info"),
    ser("Сеть реакций", nn_ret, "success"),
    ser("Спринт 3 ATR", s3_ret, "danger"),
    ser("Правила", rules_top, "info"),
    ser("Сеть реакций", nn_top, "success"),
    ser("Правила", rules_dd, "info"),
    ser("Сеть реакций", nn_dd, "success"),
    ser("Спринт 3 ATR", s3_dd, "danger"),
)
tsx = tsx.replace("%%", "%")
out = Path(r"C:\Users\123\.cursor\projects\c-Users-123-cursor-skills-bybit-top25-scan\canvases\pitbull-sprint-nn.canvas.tsx")
out.write_text(tsx, encoding="utf-8", newline="\n")
print(out)
print("equity", aligned(rules_c)[-1], aligned(nn_c)[-1], aligned(s3_c)[-1])
