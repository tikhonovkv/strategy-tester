# -*- coding: utf-8 -*-
import json
from pathlib import Path

lab = Path(r"C:\Users\123\.cursor\skills\bybit-top25-scan\strategy_tester_repo\python_research\futures")
truth = json.loads((lab / "_year_truth_audit.json").read_text(encoding="utf-8"))
honesty = json.loads((lab / "_rule_honesty.json").read_text(encoding="utf-8"))

keys = ["2022", "2023", "2024", "2025", "2026H1", "2026Q"]
cats = ["2022", "2023", "2024", "2025", "2026 до июля", "Квартал"]

flat = truth["flat"]
sides = truth["sides"]
asym = truth["asymmetry"]
btc = honesty["btc_move"]

flat_mean = [flat[k]["mean"] for k in keys]
flat_ex5 = [flat[k]["ex5"] if flat[k]["ex5"] is not None else 0 for k in keys]
n_trades = [flat[k]["n"] for k in keys]
winrate = [flat[k]["winrate"] for k in keys]
long_mean = [sides[k]["long"]["mean"] if sides[k]["long"]["n"] else 0 for k in keys]
short_mean = [sides[k]["short"]["mean"] if sides[k]["short"]["n"] else 0 for k in keys]
btc_move = [btc.get(k, 0) for k in keys]
plain_ret = [honesty["plain"][k]["ret"] for k in keys]
rule_ret = [honesty["with_rule"][k]["ret"] for k in keys]

rows = []
for k, cat in zip(keys, cats):
    a = asym[k]
    rows.append(
        '["%s", "%d", "%d", "%+.1f", "%+.2f", "%+.2f"]'
        % (cat, a["n_total"], a["n_dropped"], a["dropped_total"], a["all_mean"], a["kept_mean"] if a["kept_mean"] is not None else 0)
    )


def ser(name, data, tone=None):
    bit = ', tone: "%s"' % tone if tone else ""
    return '{ name: "%s", data: [%s]%s }' % (name, ", ".join(str(v) for v in data), bit)


tsx = """import { BarChart, Callout, Divider, Grid, H1, H2, Stack, Stat, Table, Text } from "cursor/canvas";

const years = [%s];

export default function YearTruthAudit() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>Откуда на самом деле берётся прибыль по годам</H1>
        <Text tone="secondary">
          790 сделок Шварца с фиксированным риском 100 долларов на сделку: без сложного процента, без очереди слотов. Так виден чистый результат правила, а не эффект накопленного банка.
        </Text>
      </Stack>

      <Callout tone="warning" title="Вы правы: плюс держится на 2022, 2025 и квартале">
        При равной ставке в каждой сделке 2023, 2024 и первая половина 2026 убыточны. Положительны только 2022, 2025 и текущий квартал. Прошлые проценты по годам выглядели лучше, потому что считались со сложным процентом и разным размером позиции.
      </Callout>

      <Grid columns={4} gap={14}>
        <Stat value="3 из 6" label="Прибыльных периодов при равной ставке" tone="warning" />
        <Stat value="60" label="Сделок за весь 2024 год" tone="danger" />
        <Stat value="−78 против −41" label="2024: средняя сделка с правилом и без" tone="danger" />
        <Stat value="16-26%%" label="Доля прибыльных сделок во всех годах" />
      </Grid>

      <Divider />

      <H2>Средняя сделка года при риске 100 долларов</H2>
      <BarChart
        categories={years}
        height={260}
        valuePrefix="$"
        series={[
          %s,
          %s,
        ]}
      />
      <Text tone="tertiary" size="small">
        Серая серия — та же средняя, но без пяти лучших сделок года. Она отрицательна везде, кроме 2025 года. Это и есть зависимость от нескольких крупных ходов: обычная сделка в среднем теряет деньги.
      </Text>

      <H2>Средняя сделка по сторонам и движение биткоина за период</H2>
      <BarChart
        categories={years}
        height={240}
        series={[
          %s,
          %s,
          %s,
        ]}
      />
      <Text tone="tertiary" size="small">
        Доллары на сделку для сторон, проценты для биткоина. Шорты зарабатывают в 2022 (биткоин −64%%) и в 2025. В 2024 году, когда биткоин вырос на 121%%, обе стороны в минусе: трендовых входов было всего 60 за год.
      </Text>

      <H2>Число сделок и доля прибыльных, по годам</H2>
      <BarChart
        categories={years}
        height={220}
        series={[
          %s,
          %s,
        ]}
      />
      <Text tone="tertiary" size="small">
        В 2024 году генератор дал 60 сделок против 158-180 в других годах. При доле побед 15%% это примерно 9 прибыльных сделок за год: на такой выборке год решает случай, а не правило.
      </Text>

      <H2>Правило асимметрии лонгов: что оно выбросило</H2>
      <Table
        headers={["Период", "Всего сделок", "Выброшено", "Сумма выброшенного", "Средняя до правила", "Средняя после"]}
        columnAlign={["left", "right", "right", "right", "right", "right"]}
        striped
        rowTone={["neutral", "success", "danger", "success", "success", "success"]}
        rows={[
          %s
        ]}
      />
      <Text tone="tertiary" size="small">
        В 2024 году правило выбросило 25 сделок, которые в сумме дали плюс 300 долларов, и средняя сделка ухудшилась с −41 до −78 долларов. В остальных пяти периодах оно убирало убыточные лонги. Правило помогает в пяти периодах из шести и вредит ровно в том году, который и так самый слабый.
      </Text>

      <H2>Доходность портфеля 7x: с правилом и без</H2>
      <BarChart
        categories={years}
        height={240}
        valueSuffix="%%"
        series={[
          %s,
          %s,
        ]}
      />
      <Text tone="tertiary" size="small">
        Источник: schwartz_production.simulate_window, комиссия 0.11%% и 3 б.п. проскальзывания. Правило прибавляет в 2022, 2023, 2025, первой половине 2026 и квартале, и отнимает 0.5 процентного пункта в 2024. Данные SUI начинаются только с мая 2023 года, поэтому 2022 год торгуется шестью монетами, а не семью.
      </Text>
    </Stack>
  );
}
""" % (
    ", ".join('"%s"' % c for c in cats),
    ser("Средняя сделка", flat_mean, "info"),
    ser("Средняя без пяти лучших", flat_ex5, "danger"),
    ser("Лонги, доллары", long_mean, "success"),
    ser("Шорты, доллары", short_mean, "danger"),
    ser("Биткоин за период, проценты", btc_move, "neutral"),
    ser("Сделок за период", n_trades, "info"),
    ser("Доля прибыльных, проценты", winrate, "neutral"),
    ",\n          ".join(rows),
    ser("Без правила", plain_ret, "neutral"),
    ser("С правилом асимметрии", rule_ret, "success"),
)

out = Path(r"C:\Users\123\.cursor\projects\c-Users-123-cursor-skills-bybit-top25-scan\canvases\year-truth-audit.canvas.tsx")
out.write_text(tsx.replace("%%", "%"), encoding="utf-8", newline="\n")
print("wrote", out)
