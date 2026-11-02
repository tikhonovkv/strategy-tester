# -*- coding: utf-8 -*-
import json
from datetime import datetime, timezone
from pathlib import Path

lab = Path(r"C:\Users\123\.cursor\skills\bybit-top25-scan\strategy_tester_repo\python_research\futures")
payload = json.loads((lab / "_entry_precursor.json").read_text(encoding="utf-8"))
report, curves = payload["report"], payload["curves"]
sep = report["separation"]

base = curves["all"]
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


def line(key, title, tone=None):
    data = ", ".join(str(at(curves[key], t)) for t in times)
    bit = ', tone: "%s"' % tone if tone else ""
    return '{ name: "%s", data: [%s]%s }' % (title, data, bit)


keys = ["2022", "2023", "2024", "2025", "2026H1", "2026Q"]
cats = ["2022", "2023", "2024", "2025", "2026 до июля", "Квартал"]
shown = [
    ("all", "Все входы", "info"),
    ("vol15", "Объём от 1.5", None),
    ("leader2", "Топ-2 монеты", "warning"),
    ("coil08", "Сжатие диапазона", None),
    ("evict", "Замена тухлого слота", "success"),
]


def bars(field):
    lines = []
    for key, title, tone in shown:
        data = ", ".join(str(report[key]["portfolio"][k][field]) for k in keys)
        bit = ', tone: "%s"' % tone if tone else ""
        lines.append('{ name: "%s", data: [%s]%s }' % (title, data, bit))
    return ",\n          ".join(lines)


feat_names = ["объём", "тело свечи", "закрытие у края", "импульс бара", "сжатие 12ч"]
feat_keys = ["vol_surge", "body", "close_loc", "thrust", "coil"]
win = [sep[k]["win"] for k in feat_keys]
lose = [sep[k]["lose"] for k in feat_keys]
labels = [datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%y-%m") for t in times]
final_all = format(report["all"]["portfolio"]["full"]["final"], ",.0f").replace(",", " ")
final_evict = format(report["evict"]["portfolio"]["full"]["final"], ",.0f").replace(",", " ")

tsx = """import { BarChart, Divider, Grid, H1, H2, LineChart, Stack, Stat, Text } from "cursor/canvas";

const dates = [%s];
const years = [%s];

export default function EntryPrecursors() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>Признаки бара входа</H1>
        <Text tone="secondary">
          790 сигналов Шварца, признаки посчитаны на самой свече входа и на 24 часах до неё. Жёсткий отбор по объёму, телу свечи, краю закрытия и силе против биткоина не делает обычную сделку прибыльной и срезает хвосты, на которых держится счёт.
        </Text>
      </Stack>
      <Grid columns={3} gap={16}>
        <Stat value="1.31 против 1.08" label="Объём победителей и проигравших" />
        <Stat value="$%s" label="Все входы, весь путь" />
        <Stat value="$%s" label="Замена тухлого слота, весь путь" />
      </Grid>
      <Divider />
      <H2>Медиана признака на входе</H2>
      <BarChart
        categories={%s}
        height={240}
        series={[
          { name: "Плюсовые сделки", data: [%s], tone: "success" },
          { name: "Убыточные сделки", data: [%s], tone: "danger" },
        ]}
      />
      <Text tone="tertiary" size="small">
        Объём делится на свою медиану за 24 часа. Тело и закрытие у края — доля часового диапазона. Сжатие — ширина 12 часов в долях ATR. Тело свечи и край закрытия у плюса и минуса почти совпадают.
      </Text>
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
        Ось времени: месяц точки с 2022 по ноябрь 2026. Отбор по объёму от 1.5 и требование быть в двух лидерах корзины оставляют счёт внизу. Замена тухлого слота идёт рядом с полным списком, но ниже.
      </Text>
      <H2>Доходность свежего старта, процент</H2>
      <BarChart
        categories={years}
        height={240}
        valueSuffix="%%"
        series={[
          %s
        ]}
      />
      <H2>Максимальная просадка свежего старта, процент</H2>
      <BarChart
        categories={years}
        height={220}
        valueSuffix="%%"
        series={[
          %s
        ]}
      />
      <Text tone="tertiary" size="small">
        Источник: локальный часовой кэш Bybit и свечи open-high-low-close. К каждому исходу добавлены 6 б.п. проскальзывания сверх комиссии 0.11%%. Средняя сделка без пяти лучших остаётся убыточной во всех годах, кроме крошечного плюса 2025-го у полного списка. Боевой бот не менялся.
      </Text>
    </Stack>
  );
}
""" % (
    ", ".join('"%s"' % x for x in labels),
    ", ".join('"%s"' % x for x in cats),
    final_all,
    final_evict,
    json.dumps(feat_names, ensure_ascii=False),
    ", ".join(str(v) for v in win),
    ", ".join(str(v) for v in lose),
    line("all", "Все входы", "info"),
    line("vol15", "Объём от 1.5", "warning"),
    line("evict", "Замена тухлого слота", "success"),
    bars("ret"),
    bars("dd"),
)
out = Path(r"C:\Users\123\.cursor\projects\c-Users-123-cursor-skills-bybit-top25-scan\canvases\entry-precursors.canvas.tsx")
out.write_text(tsx.replace("%%", "%"), encoding="utf-8", newline="\n")
print(out)
