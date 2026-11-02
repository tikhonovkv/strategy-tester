# -*- coding: utf-8 -*-
import json
from pathlib import Path

src = Path(r"C:\Users\123\.cursor\skills\bybit-top25-scan\strategy_tester_repo\python_research\futures\_anatomy_top25.json")
report = json.loads(src.read_text(encoding="utf-8"))["report"]
top, rest = report["top"], report["rest"]
hours = [str(i) for i in range(1, len(report["path_top"]) + 1)]


def series_line(name, data, tone=None):
    body = ", ".join(str(v) for v in data)
    tone_bit = ', tone: "%s"' % tone if tone else ""
    return '{ name: "%s", data: [%s]%s }' % (name, body, tone_bit)


rows = []
for r in report["passport"]:
    rows.append(
        '["%s", "%s", "%s", "%s", "%s", "%s", "%s", "%s", "%s"]'
        % (
            r["time"][:10],
            r["coin"],
            "лонг" if r["side"] == "LONG" else "шорт",
            r["hold_h"],
            r["reason"],
            r["net_pct"],
            r["peak_atr"],
            r["give_atr"],
            r["pulls3"],
        )
    )

tsx = """import { BarChart, Divider, H1, H2, LineChart, Stack, Stat, Table, Text, Grid } from "cursor/canvas";

const hours = [%s];

export default function Top25Anatomy() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>Анатомия 25 лучших сделок</H1>
        <Text tone="secondary">
          790 сигналов Шварца с 2022 по ноябрь 2026. Лучшие 25 — по чистому ходу цены, не по размеру ставки. Неделя до входа почти не отличает их от остальных. Отличает первый час и то, что внутри удержания сидит несколько отдельных волн.
        </Text>
      </Stack>
      <Grid columns={3} gap={16}>
        <Stat value="92%% против 52%%" label="+1 ATR за первые 6 часов" tone="success" />
        <Stat value="88%% против 32%%" label="Не ниже −0.5 ATR за 6 часов" />
        <Stat value="58%%" label="Пять лучших в долларовом плюсе счёта" />
      </Grid>
      <Divider />
      <H2>Медианный ход после входа, в ATR</H2>
      <LineChart
        categories={hours}
        height={280}
        valueSuffix=" ATR"
        fill={false}
        beginAtZero={false}
        series={[
          %s,
          %s,
        ]}
      />
      <Text tone="tertiary" size="small">
        Ось времени: час после сигнала. Ноль — цена входа. У лучших медиана уже положительная на первом часе. У остальных медиана уходит ниже нуля и не возвращается.
      </Text>
      <H2>Через сколько часов ход достигает порога</H2>
      <BarChart
        categories={["до +1 ATR", "до +2 ATR", "до +3 ATR"]}
        height={220}
        valueSuffix=" ч"
        series={[
          %s,
          %s,
        ]}
      />
      <Text tone="tertiary" size="small">
        Медиана только среди сделок, которые вообще дошли до порога. Лучшие доходят за 1, 2 и 3 часа. Остальные — за 4, 9 и 12 часов.
      </Text>
      <H2>Сколько волн сидит внутри одной сделки</H2>
      <BarChart
        categories={["откат на 1 ATR", "откат на 2 ATR", "откат на 3 ATR"]}
        height={220}
        valueSuffix=" волн"
        series={[
          %s,
          %s,
        ]}
      />
      <Text tone="tertiary" size="small">
        Медианное число импульсов, после которых цена отдавала указанный ход и снова обновляла пик. У лучших за 95 часов таких волн по 3 ATR — три. У остальных пика в 3 ATR почти нет.
      </Text>
      <H2>Паспорт 25 лучших</H2>
      <Table
        headers={["Дата", "Монета", "Сторона", "Часы", "Выход", "Ход %%", "Пик, ATR", "Отдача, ATR", "Волн по 3 ATR"]}
        columnAlign={["left", "left", "left", "right", "left", "right", "right", "right", "right"]}
        striped
        rows={[
          %s
        ]}
      />
      <Text tone="tertiary" size="small">
        Источник: локальный часовой кэш Bybit, выход текущих правил. Объём на баре входа у лучших в 2.1 раза выше своей суточной нормы, у остальных в 1.1 раза. Ширина рынка, сжатие ATR за неделю и фандинг почти совпадают: медианная ширина 6 из 7 монет в обеих группах. Пять лучших забирают 58%% долларового плюса счёта, но только 20%% суммарного хода цены: концентрация появляется из размера ставки, а не из того, что остальных плюсов нет.
      </Text>
    </Stack>
  );
}
""" % (
    ", ".join('"%s"' % h for h in hours),
    series_line("25 лучших", report["path_top"], "success"),
    series_line("Остальные 765", report["path_rest"], "danger"),
    series_line("25 лучших", [top["h1"], top["h2"], top["h3"]], "success"),
    series_line("Остальные", [rest["h1"], rest["h2"], rest["h3"]], "neutral"),
    series_line("25 лучших", [top["pulls"], top["pulls2"], top["pulls3"]], "success"),
    series_line("Остальные", [rest["pulls"], rest["pulls2"], rest["pulls3"]], "neutral"),
    ",\n          ".join(rows),
)
tsx = tsx.replace("%%", "%")
out = Path(r"C:\Users\123\.cursor\projects\c-Users-123-cursor-skills-bybit-top25-scan\canvases\top25-anatomy.canvas.tsx")
out.write_text(tsx, encoding="utf-8", newline="\n")
print(out, "rows", len(rows), "hours", len(hours))
