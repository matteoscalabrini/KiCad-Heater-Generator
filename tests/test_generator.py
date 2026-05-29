import unittest

from heater_generator.generator import HeaterParameters, generate_heater, outline_overflow_mm, polyline_length


class HeaterGeneratorTests(unittest.TestCase):
    def test_serpentine_trims_to_target(self):
        result = generate_heater(
            HeaterParameters(
                voltage_v=5.0,
                wattage_w=2.5,
                track_width_mm=0.25,
                clearance_mm=0.25,
                copper_thickness_um=35,
                outline="rectangle",
                curve="serpentine",
                width_mm=100,
                height_mm=80,
            )
        )
        self.assertGreater(len(result.points), 2)
        self.assertAlmostEqual(polyline_length(result.points), result.target_length_mm, places=5)
        self.assertAlmostEqual(result.resistance_ohm, result.target_resistance_ohm, places=5)

    def test_circle_coil_stays_inside_circle(self):
        params = HeaterParameters(
            outline="circle",
            curve="coil",
            width_mm=30,
            height_mm=30,
            track_width_mm=0.3,
            clearance_mm=0.3,
            margin_mm=1,
            trim_to_target=False,
        )
        result = generate_heater(params)
        center = result.params.width_mm / 2.0
        radius = center - result.params.margin_mm
        for x, y in result.points:
            self.assertLessEqual(((x - center) ** 2 + (y - center) ** 2) ** 0.5, radius)

    def test_hilbert_order_increases_available_length(self):
        base = HeaterParameters(curve="hilbert", width_mm=30, height_mm=30, trim_to_target=False)
        short = generate_heater(HeaterParameters(**{**base.__dict__, "hilbert_order": 2}))
        long = generate_heater(HeaterParameters(**{**base.__dict__, "hilbert_order": 4}))
        self.assertGreater(long.path_length_mm, short.path_length_mm)

    def test_adaptive_fill_adjusts_clearance_to_match_target(self):
        result = generate_heater(HeaterParameters(adaptive_fill=True))
        self.assertAlmostEqual(result.wattage_w, result.params.wattage_w, delta=0.2)
        self.assertGreaterEqual(result.params.track_width_mm, 0.25)
        self.assertGreaterEqual(result.params.clearance_mm, 0.25)

    def test_outline_overflow_reports_stroke_spill(self):
        overflow = outline_overflow_mm(
            [(0.0, 5.0), (10.0, 5.0)],
            HeaterParameters(width_mm=10.0, height_mm=10.0),
            2.0,
        )
        self.assertAlmostEqual(overflow, 1.0)


if __name__ == "__main__":
    unittest.main()
