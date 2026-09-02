"""Testy jádra (bez GUI): unittest, spustitelné přes `python -m unittest discover`."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine_generator import curve, dataimport, engfile
from engine_generator.config import Config, PresetStore

SAMPLE_ENG = """Gragas 40mm 43.4 167.0 P 0.166 0.375 CRS
0 0
0.25 18
0.5 32
1 60
2 63
3.5 1
3.6 0
;
"""


class EngFileTest(unittest.TestCase):
    def test_parse_header_with_space_in_name(self):
        spec, points = engfile.parse_eng(SAMPLE_ENG)
        self.assertEqual(spec.name, "Gragas 40mm")
        self.assertAlmostEqual(spec.diameter_mm, 43.4)
        self.assertAlmostEqual(spec.length_mm, 167.0)
        self.assertEqual(spec.delays, "P")
        self.assertAlmostEqual(spec.propellant_kg, 0.166)
        self.assertAlmostEqual(spec.total_kg, 0.375)
        self.assertEqual(spec.manufacturer, "CRS")
        self.assertEqual(len(points), 7)

    def test_roundtrip(self):
        spec, points = engfile.parse_eng(SAMPLE_ENG)
        again_spec, again_points = engfile.parse_eng(engfile.build_eng_text(spec, points))
        self.assertEqual(again_spec.name, spec.name)
        self.assertEqual(again_points, points)

    def test_motor_class(self):
        self.assertEqual(engfile.motor_class(2.4), "A")
        self.assertEqual(engfile.motor_class(5.0), "B")
        self.assertEqual(engfile.motor_class(224.0), "H")
        self.assertEqual(engfile.motor_class(0.0), "-")

    def test_summary_values(self):
        _spec, points = engfile.parse_eng(SAMPLE_ENG)
        info = engfile.summary(points)
        self.assertAlmostEqual(info["burn_s"], 3.6)
        self.assertEqual(info["class"], "G")
        self.assertTrue(info["designation"].startswith("G"))

    def test_validation_catches_bad_input(self):
        spec = engfile.MotorSpec(name="", diameter_mm=0, length_mm=0,
                                 propellant_kg=0.5, total_kg=0.2)
        errors, _warnings = engfile.validate(spec, [(0.0, 0.0), (1.0, 0.0)])
        self.assertTrue(any("název" in e.lower() for e in errors))
        self.assertTrue(any("průměr" in e.lower() for e in errors))
        self.assertTrue(any("nulová" in e.lower() for e in errors))

    def test_validation_rejects_decreasing_time(self):
        spec = engfile.MotorSpec("X", 40, 100, "P", 0.1, 0.2, "CRS")
        errors, _ = engfile.validate(spec, [(0.0, 0.0), (0.5, 10.0), (0.4, 0.0)])
        self.assertTrue(any("rostoucí" in e for e in errors))

    def test_diacritics_are_stripped_on_write(self):
        spec = engfile.MotorSpec(name="Tomášův motor", diameter_mm=40, length_mm=100,
                                 propellant_kg=0.1, total_kg=0.2, manufacturer="Křídla",
                                 comments=["měřeno na stavu"])
        text = engfile.build_eng_text(spec, [(0.0, 0.0), (1.0, 10.0), (2.0, 0.0)])
        text.encode("ascii")  # nesmí vyhodit výjimku
        self.assertIn("Tomasuv motor", text)
        self.assertIn("Kridla", text)
        self.assertIn("mereno na stavu", text)

    def test_suggest_filename_is_safe(self):
        spec = engfile.MotorSpec(name="Gragas 40mm/test")
        self.assertEqual(engfile.suggest_filename(spec, []), "Gragas_40mm_test.eng")


class CurveTest(unittest.TestCase):
    def synthetic(self):
        """Lichoběžníkový tah 100 N mezi 1 a 3 s, plus klidový offset a šum na začátku."""
        series = []
        for index in range(0, 500):
            t = index * 0.01
            if t < 1.0 or t > 3.0:
                thrust = 0.0
            elif t < 1.2:
                thrust = 100.0 * (t - 1.0) / 0.2
            elif t > 2.8:
                thrust = 100.0 * (3.0 - t) / 0.2
            else:
                thrust = 100.0
            series.append((t, thrust + 2.0))  # 2 N klidový offset
        return series

    def test_baseline_and_trim(self):
        points = curve.process(self.synthetic(), curve.ProcessOptions(mode="raw"))
        self.assertLess(points[0][0], 0.05)
        self.assertEqual(points[-1][1], 0.0)
        self.assertLess(abs(engfile.peak_thrust(points) - 100.0), 1.0)

    def test_fixed_step_resample(self):
        options = curve.ProcessOptions(mode="step", step_ms=200)
        points = curve.process(self.synthetic(), options)
        gaps = [round(b[0] - a[0], 6) for a, b in zip(points, points[1:])]
        # Všechny kroky jsou 200 ms; poslední bod dosedá přesně na konec hoření.
        self.assertTrue(all(g <= 0.2 + 1e-9 for g in gaps), gaps)
        self.assertGreaterEqual(sum(1 for g in gaps if abs(g - 0.2) < 1e-6), len(gaps) - 2)

    def test_impulse_is_preserved_when_reducing(self):
        raw = self.synthetic()
        detailed = curve.process(raw, curve.ProcessOptions(mode="raw"))
        reduced = curve.process(raw, curve.ProcessOptions(mode="reduce", max_points=8))
        self.assertLessEqual(len(reduced), 10)
        self.assertAlmostEqual(engfile.total_impulse(detailed),
                               engfile.total_impulse(reduced), delta=1.0)

    def test_burn_window_ignores_short_spike(self):
        series = [(0.0, 0.0), (0.1, 80.0), (0.2, 0.0)]  # špička zážehové linky
        series += [(0.3 + i * 0.01, 0.0) for i in range(100)]
        series += [(1.3 + i * 0.01, 100.0) for i in range(100)]
        series += [(2.3 + i * 0.01, 0.0) for i in range(20)]
        points = curve.process(series, curve.ProcessOptions(mode="raw", subtract_baseline=False))
        self.assertLess(engfile.burn_time(points), 1.5)
        self.assertAlmostEqual(engfile.peak_thrust(points), 100.0, delta=0.1)

    def test_raw_mode_keeps_every_sample(self):
        raw = self.synthetic()
        points = curve.process(raw, curve.ProcessOptions(mode="raw", trim_to_burn=False,
                                                         subtract_baseline=False,
                                                         shift_to_zero=False))
        # Žádné převzorkování: časy odpovídají vzorkům ze zdroje.
        source_times = [round(t, 6) for t, _ in raw]
        for time_s, _thrust in points:
            if time_s in source_times:
                continue
            self.assertGreater(time_s, source_times[-1])  # jen doplněná koncová nula
        self.assertGreaterEqual(len(points), len(raw))

    def test_make_grid(self):
        grid = curve.make_grid(500, 2.0)
        self.assertEqual([t for t, _ in grid], [0.0, 0.5, 1.0, 1.5, 2.0])

    def test_interpolate(self):
        series = [(0.0, 0.0), (1.0, 10.0)]
        self.assertAlmostEqual(curve.interpolate(series, 0.5), 5.0)
        self.assertAlmostEqual(curve.interpolate(series, -1.0), 0.0)
        self.assertAlmostEqual(curve.interpolate(series, 5.0), 10.0)


class ImportTest(unittest.TestCase):
    def test_csv_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.csv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("Time [s];Thrust [N]\n0;0\n0.5;25\n1.0;0\n")
            workbook = dataimport.read_any(path)
            sheet = dataimport.best_data_sheet(workbook)
            time_col, thrust_col, scale = dataimport.guess_columns(sheet)
            series = dataimport.extract_series(sheet, time_col, thrust_col, scale)
            self.assertEqual(series, [(0.0, 0.0), (0.5, 25.0), (1.0, 0.0)])

    def test_milliseconds_column_is_scaled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.csv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("Time [ms],Thrust [N]\n0,0\n500,25\n1000,0\n")
            workbook = dataimport.read_any(path)
            sheet = workbook.sheets[0]
            time_col, thrust_col, scale = dataimport.guess_columns(sheet)
            self.assertAlmostEqual(scale, 0.001)
            series = dataimport.extract_series(sheet, time_col, thrust_col, scale)
            self.assertEqual(series[-1], (1.0, 0.0))

    def test_metadata_units(self):
        self.assertAlmostEqual(dataimport._parse_quantity("278.0 g", "propellant_kg"), 0.278)
        self.assertAlmostEqual(dataimport._parse_quantity("0.5 kg", "total_kg"), 0.5)
        self.assertAlmostEqual(dataimport._parse_quantity("4 cm", "diameter_mm"), 40.0)


class ConfigTest(unittest.TestCase):
    def test_settings_survive_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            cfg = Config(path)
            cfg["output_dir"] = os.path.join(tmp, "ThrustCurves")
            cfg["step_ms"] = 500
            cfg.save()
            self.assertEqual(Config(path)["step_ms"], 500)
            self.assertTrue(Config(path)["output_dir"].endswith("ThrustCurves"))

    def test_presets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "presets.json")
            store = PresetStore(path)
            store.put("Gragas", {"name": "Gragas", "diameter_mm": "43.4"})
            self.assertEqual(PresetStore(path).get("Gragas")["diameter_mm"], "43.4")
            store.delete("Gragas")
            self.assertEqual(PresetStore(path).names(), [])


if __name__ == "__main__":
    unittest.main()
