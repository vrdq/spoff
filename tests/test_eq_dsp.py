import unittest
import math
from spoff.eq import (
    FilterType,
    BiquadCoefficients,
    BiquadFilter,
    EQBand,
    EQPreset,
    ParametricEQEngine,
    SAMSUNG_AKG_REFERENCE_PRESET,
    HARMAN_IN_EAR_2019_PRESET,
    BUILTIN_PRESETS,
    format_gain_bar,
    render_braille_curve,
)
from spoff.storage import load_eq_settings, save_eq_settings
from spoff.player import MPVController


class TestParametricEQDSP(unittest.TestCase):
    def test_rbj_peaking_biquad_coefficients(self):
        """Validates Peaking EQ biquad filter calculations against RBJ cookbook formulae."""
        band = EQBand(
            index=1,
            filter_type=FilterType.PEAKING,
            frequency=1000.0,
            gain_db=6.0,
            q=1.0,
            enabled=True
        )
        coeffs = band.calculate_coefficients(sample_rate=48000.0)
        # Verify transfer function magnitude at center frequency
        mag_f0 = coeffs.magnitude_db(1000.0, sample_rate=48000.0)
        self.assertAlmostEqual(mag_f0, 6.0, delta=0.05)

        # Far from center frequency, peaking filter gain must tend to 0 dB
        mag_dc = coeffs.magnitude_db(20.0, sample_rate=48000.0)
        mag_high = coeffs.magnitude_db(20000.0, sample_rate=48000.0)
        self.assertAlmostEqual(mag_dc, 0.0, delta=0.2)
        self.assertAlmostEqual(mag_high, 0.0, delta=0.2)

    def test_rbj_low_shelf_biquad_coefficients(self):
        """Validates Low-Shelf biquad filter calculations."""
        band = EQBand(
            index=1,
            filter_type=FilterType.LOW_SHELF,
            frequency=100.0,
            gain_db=5.0,
            q=0.71,
            enabled=True
        )
        coeffs = band.calculate_coefficients(sample_rate=48000.0)
        # Deep bass should be boosted by +5 dB
        mag_bass = coeffs.magnitude_db(25.0, sample_rate=48000.0)
        self.assertAlmostEqual(mag_bass, 5.0, delta=0.2)

        # High frequencies should have unity gain (0 dB)
        mag_treble = coeffs.magnitude_db(10000.0, sample_rate=48000.0)
        self.assertAlmostEqual(mag_treble, 0.0, delta=0.1)

    def test_rbj_high_shelf_biquad_coefficients(self):
        """Validates High-Shelf biquad filter calculations."""
        band = EQBand(
            index=1,
            filter_type=FilterType.HIGH_SHELF,
            frequency=10000.0,
            gain_db=4.0,
            q=0.71,
            enabled=True
        )
        coeffs = band.calculate_coefficients(sample_rate=48000.0)
        # Low frequencies should have unity gain (0 dB)
        mag_bass = coeffs.magnitude_db(100.0, sample_rate=48000.0)
        self.assertAlmostEqual(mag_bass, 0.0, delta=0.1)

        # Air/Treble frequencies should be boosted by +4 dB
        mag_air = coeffs.magnitude_db(16000.0, sample_rate=48000.0)
        self.assertAlmostEqual(mag_air, 4.0, delta=0.3)

    def test_transposed_direct_form_ii_filtering(self):
        """Verifies real-time sample processing stability in Transposed Direct Form II."""
        band = EQBand(
            index=1,
            filter_type=FilterType.PEAKING,
            frequency=1000.0,
            gain_db=3.0,
            q=1.0,
            enabled=True
        )
        coeffs = band.calculate_coefficients(sample_rate=48000.0)
        filt = BiquadFilter(coeffs)

        # Process a 1000 Hz sine wave
        sr = 48000.0
        num_samples = 480
        sine_in = [math.sin(2.0 * math.pi * 1000.0 * n / sr) for n in range(num_samples)]
        filtered = filt.process_buffer(sine_in, channel=0)

        # After transient settlement, peak amplitude should be amplified by ~10^(3/20) = 1.412
        steady_state_peaks = [abs(s) for s in filtered[240:]]
        max_peak = max(steady_state_peaks)
        self.assertAlmostEqual(max_peak, 1.412, delta=0.08)

        # Verify anti-denormal flush
        filt.process_sample(1e-25, channel=0)
        self.assertFalse(math.isnan(filt.s1_l))
        self.assertFalse(math.isinf(filt.s1_l))

    def test_samsung_akg_reference_preset_specification(self):
        """
        Validates the flagship Samsung AKG Master Reference calibration preset
        for dual-driver Samsung EO-IG955 IEMs against strict user specifications.
        """
        preset = SAMSUNG_AKG_REFERENCE_PRESET
        self.assertEqual(preset.name, "Samsung AKG Master Reference")
        self.assertEqual(preset.preamp_db, -5.0)
        self.assertEqual(len(preset.bands), 10)

        # Expected specifications for all 10 acoustic bands
        expected = [
            (1, FilterType.LOW_SHELF, 65.0, 4.5, 0.70),
            (2, FilterType.PEAKING, 145.0, -4.5, 0.50),
            (3, FilterType.PEAKING, 230.0, -1.8, 1.40),
            (4, FilterType.PEAKING, 780.0, 4.2, 0.65),
            (5, FilterType.PEAKING, 1750.0, -1.2, 1.80),
            (6, FilterType.PEAKING, 2900.0, -2.2, 3.20),
            (7, FilterType.PEAKING, 4600.0, 2.2, 3.50),
            (8, FilterType.PEAKING, 6400.0, 2.8, 4.00),
            (9, FilterType.PEAKING, 10000.0, -7.5, 2.20),
            (10, FilterType.HIGH_SHELF, 13500.0, 3.0, 0.70),
        ]

        for b, (exp_idx, exp_type, exp_f, exp_g, exp_q) in zip(preset.bands, expected):
            self.assertEqual(b.index, exp_idx)
            self.assertEqual(b.filter_type, exp_type)
            self.assertAlmostEqual(b.frequency, exp_f, delta=0.1)
            self.assertAlmostEqual(b.gain_db, exp_g, delta=0.01)
            self.assertAlmostEqual(b.q, exp_q, delta=0.01)
            self.assertTrue(b.enabled)

    def test_samsung_akg_digital_headroom_anti_clipping(self):
        """
        Validates that the active Preamp gain stage attenuates composite gain so that
        no frequency in the 20 Hz - 20 kHz audio range ever exceeds 0 dBFS.
        """
        engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET)
        peak_gain_dbfs, peak_freq = engine.calculate_peak_gain(num_points=500)

        # Crucial Requirement: Boosted bands must NEVER exceed 0 dBFS
        self.assertLess(peak_gain_dbfs, 0.0)
        # For Samsung AKG with -5.0dB preamp, max gain is around -0.89 dBFS at 20Hz
        self.assertAlmostEqual(peak_gain_dbfs, -0.89, delta=0.2)
        self.assertTrue(peak_gain_dbfs < 0.0, "Composite transfer function exceeded 0 dBFS ceiling!")

    def test_auto_preamp_headroom_calculation(self):
        """Tests calculation of exact required attenuation for digital headroom."""
        engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET)
        # Strip preamp to simulate unattenuated boost
        engine.set_preamp(0.0)
        peak_unattenuated, _ = engine.calculate_peak_gain(num_points=300)
        self.assertGreater(peak_unattenuated, 0.0)

        # Compute auto-headroom with 0.5 dB safety margin
        recommended_preamp = engine.auto_preamp_headroom(margin_db=0.5)
        self.assertLess(recommended_preamp, 0.0)

        # Apply recommended preamp and verify peak gain is safely below 0 dBFS
        engine.set_preamp(recommended_preamp)
        peak_safe, _ = engine.calculate_peak_gain(num_points=300)
        self.assertLess(peak_safe, -0.4)

    def test_bypass_ab_testing(self):
        """Validates pop-free A-B bypass toggle functionality."""
        engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET)
        self.assertFalse(engine.bypassed)

        # When active, gain varies across spectrum
        g_65 = engine.get_magnitude_at_freq(65.0)
        self.assertNotEqual(g_65, 0.0)

        # Toggle bypass
        bypassed = engine.toggle_bypass()
        self.assertTrue(bypassed)
        self.assertTrue(engine.bypassed)

        # In bypass mode, all frequencies must report 0.0 dB (bit-perfect flat pass-through)
        for freq in [20.0, 100.0, 1000.0, 10000.0, 20000.0]:
            self.assertEqual(engine.get_magnitude_at_freq(freq), 0.0)

        # FFmpeg filter string must be empty in bypass mode
        self.assertEqual(engine.to_ffmpeg_af(), "")

        # Un-bypass restores original active response
        engine.toggle_bypass()
        self.assertFalse(engine.bypassed)
        self.assertAlmostEqual(engine.get_magnitude_at_freq(65.0), g_65, delta=0.01)

    def test_ffmpeg_af_string_compilation(self):
        """Verifies compilation of the EQ filter chain into an MPV/FFmpeg audio filter graph."""
        engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET)
        af_str = engine.to_ffmpeg_af()

        # 1. Preamp must be first filter in chain
        self.assertTrue(af_str.startswith("volume=volume=-5.00dB:precision=fixed"))

        # 2. Contains all 10 biquad stages with double precision (r=f64)
        self.assertIn("lowshelf=f=65.0:t=q:w=0.70:g=4.50:r=f64", af_str)
        self.assertIn("equalizer=f=145.0:t=q:w=0.50:g=-4.50:r=f64", af_str)
        self.assertIn("equalizer=f=10000.0:t=q:w=2.20:g=-7.50:r=f64", af_str)
        self.assertIn("highshelf=f=13500.0:t=q:w=0.70:g=3.00:r=f64", af_str)

        # Count filter stages: 1 volume + 10 biquads = 11 filters
        stages = af_str.split(",")
        self.assertEqual(len(stages), 11)

    def test_serialization_and_equalizer_apo_export(self):
        """Tests round-trip serialization and EqualizerAPO format export."""
        engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET)
        d = engine.to_dict()
        restored = ParametricEQEngine.from_dict(d)

        self.assertEqual(restored.preset_name, engine.preset_name)
        self.assertEqual(restored.preamp_db, engine.preamp_db)
        self.assertEqual(len(restored.bands), len(engine.bands))

        for b1, b2 in zip(engine.bands, restored.bands):
            self.assertEqual(b1.index, b2.index)
            self.assertEqual(b1.filter_type, b2.filter_type)
            self.assertEqual(b1.frequency, b2.frequency)
            self.assertEqual(b1.gain_db, b2.gain_db)
            self.assertEqual(b1.q, b2.q)

        # EqualizerAPO export format check
        apo = engine.to_equalizer_apo()
        self.assertIn("Preamp: -5.0 dB", apo)
        self.assertIn("Filter 1: ON LSC Fc 65.0 Hz Gain +4.5 dB Q 0.70", apo)
        self.assertIn("Filter 9: ON PK Fc 10000.0 Hz Gain -7.5 dB Q 2.20", apo)
        self.assertIn("Filter 10: ON HSC Fc 13500.0 Hz Gain +3.0 dB Q 0.70", apo)

    def test_mpv_controller_eq_integration(self):
        """Validates that MPVController correctly wraps and operates the EQ engine."""
        engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET)
        player = MPVController(eq_engine=engine)
        self.assertIsNotNone(player.eq_engine)
        self.assertEqual(player.eq_engine.preset_name, "Samsung AKG Master Reference")

        # Test bypass toggle via MPVController
        bypassed = player.toggle_eq_bypass()
        self.assertTrue(bypassed)
        self.assertTrue(player.eq_engine.bypassed)

        # Test explicit set_eq_bypassed
        player.set_eq_bypassed(False)
        self.assertFalse(player.eq_engine.bypassed)

    def test_storage_roundtrip(self):
        """Validates atomic disk persistence of EQ configuration."""
        engine = ParametricEQEngine(HARMAN_IN_EAR_2019_PRESET)
        save_eq_settings(engine.to_dict())

        loaded = load_eq_settings()
        self.assertEqual(loaded.get("preset_name"), "Harman Target 2019 (In-Ear)")
        self.assertEqual(loaded.get("preamp_db"), -5.5)

        # Restore Samsung AKG preset for default experience
        save_eq_settings(ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET).to_dict())

    def test_visualization_helpers(self):
        """Tests TUI curve rendering and gain gauge formatting."""
        bar_boost = format_gain_bar(+4.5)
        self.assertIn("=", bar_boost)
        self.assertIn("#569f68", bar_boost)

        bar_cut = format_gain_bar(-4.5)
        self.assertIn("=", bar_cut)
        self.assertIn("#61afef", bar_cut)

        bar_flat = format_gain_bar(0.0)
        self.assertIn("|", bar_flat)

        engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET)
        curve = render_braille_curve(engine, width=40, height=6)
        self.assertIn("+12dB", curve)
        self.assertIn("-12dB", curve)
        self.assertIn("20Hz", curve)
        self.assertIn("20kHz", curve)


    def test_equalizer_modal_ui_workflow(self):
        """Validates EqualizerModal interactive operations within a simulated Textual App."""
        import asyncio
        from textual.app import App, ComposeResult
        from spoff.app import EqualizerModal
        from textual.widgets import DataTable, Static

        class EQTestApp(App):
            def __init__(self, engine):
                super().__init__()
                self.eq_engine = engine
                self.player = MPVController(eq_engine=self.eq_engine)

            def compose(self) -> ComposeResult:
                yield Static("Root Screen")

        async def _run():
            engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET)
            app = EQTestApp(engine)
            async with app.run_test() as pilot:
                # Push EqualizerModal
                modal = EqualizerModal(engine)
                app.push_screen(modal)
                await pilot.pause()

                table = modal.query_one("#eq-table", DataTable)
                self.assertIsNotNone(table)
                self.assertEqual(table.row_count, 11)  # 1 Preamp + 10 Bands

                # Test Gain Adjustments on Band 1
                table.move_cursor(row=1)
                initial_gain = engine.bands[0].gain_db
                modal.action_gain_up()
                self.assertAlmostEqual(engine.bands[0].gain_db, initial_gain + 0.5, delta=0.01)
                modal.action_gain_down()
                self.assertAlmostEqual(engine.bands[0].gain_db, initial_gain, delta=0.01)

                # Test Q Adjustment
                initial_q = engine.bands[0].q
                modal.action_q_up()
                self.assertAlmostEqual(engine.bands[0].q, initial_q + 0.05, delta=0.01)

                # Test Filter Type Cycle
                initial_type = engine.bands[0].filter_type
                modal.action_cycle_filter_type()
                self.assertNotEqual(engine.bands[0].filter_type, initial_type)

                # Test Toggle Band
                modal.action_toggle_band()
                self.assertFalse(engine.bands[0].enabled)
                modal.action_toggle_band()
                self.assertTrue(engine.bands[0].enabled)

                # Test Bypass Toggle
                modal.action_toggle_bypass()
                self.assertTrue(engine.bypassed)
                pill = modal.query_one("#eq-status-pill", Static)
                self.assertIn("BYPASS", str(pill.render()))
                modal.action_toggle_bypass()
                self.assertFalse(engine.bypassed)

                # Test Preset Switch
                modal.action_next_preset()
                self.assertNotEqual(engine.preset_name, "Samsung AKG Master Reference")
                modal.action_prev_preset()
                self.assertEqual(engine.preset_name, "Samsung AKG Master Reference")

                # Test Auto Headroom
                modal.action_auto_headroom()
                peak_gain, _ = engine.calculate_peak_gain()
                self.assertLess(peak_gain, 0.0)

                # Test Dismiss
                modal.action_dismiss_modal()
                await pilot.pause()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
