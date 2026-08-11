#!/usr/bin/env python3
import csv
import io
import unittest

from link_metrics import metrics, write_csv


class LinkMetricsTest(unittest.TestCase):
    def test_frozen_pin_and_edge_contract(self) -> None:
        by_name = {row.name: row for row in metrics(4)}
        self.assertEqual((by_name["parallel4"].pins, by_name["parallel4"].clock_edges_per_event), (5, 2))
        self.assertEqual((by_name["ddr2"].pins, by_name["ddr2"].clock_edges_per_event), (3, 2))
        self.assertEqual((by_name["serial1"].pins, by_name["serial1"].clock_edges_per_event), (2, 4))

    def test_exhaustive_mean_toggle_proxy(self) -> None:
        by_name = {row.name: row for row in metrics(1)}
        self.assertEqual(by_name["parallel4"].mean_total_toggles_per_event, 4.0)
        self.assertEqual(by_name["ddr2"].mean_total_toggles_per_event, 4.0)
        self.assertEqual(by_name["serial1"].mean_total_toggles_per_event, 6.0)

    def test_frequency_ratio_capacity(self) -> None:
        for ratio in (1, 2, 4):
            by_name = {row.name: row for row in metrics(ratio)}
            self.assertEqual(by_name["parallel4"].max_events_per_core_cycle, ratio)
            self.assertEqual(by_name["ddr2"].max_events_per_core_cycle, ratio)
            self.assertEqual(by_name["serial1"].max_events_per_core_cycle, ratio / 2)

    def test_csv_is_stable(self) -> None:
        stream = io.StringIO()
        write_csv(metrics(2), stream)
        rows = list(csv.DictReader(io.StringIO(stream.getvalue())))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["link"], "ddr2")
        self.assertEqual(rows[1]["max_logical_events_per_core_cycle_proxy"], "2.000")


if __name__ == "__main__":
    unittest.main()
