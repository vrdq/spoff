import unittest
import math
from spoff.eq import (
    FilterType,
    BiquadFilter,
    EQBand,
    ParametricEQEngine,
    SAMSUNG_AKG_REFERENCE_PRESET,
    HARMAN_IN_EAR_2019_PRESET,
    BUILTIN_PRESETS,
    format_gain_bar,
    render_braille_curve,
    render_blocks_curve,
    render_outline_curve,
    render_curve,
    parse_equalizer_apo,
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

        for b, (exp_idx, exp_type, exp_f, exp_g, exp_q) in zip(preset.bands, expected, strict=True):
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

        # 1. Preamp must be first filter in chain with double floating point precision
        self.assertTrue(af_str.startswith("volume=volume=-5.00dB:precision=double"))

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

        for b1, b2 in zip(engine.bands, restored.bands, strict=True):
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


class TestAutoEQParser(unittest.TestCase):
    def test_parse_autoeq_standard_format(self):
        apo_text = """
        Preamp: -5.5 dB
        Filter 1: ON PK Fc 65.0 Hz Gain +4.5 dB Q 0.70
        Filter 2: ON LSC Fc 105.0 Hz Gain -2.1 dB Q 0.71
        Filter 3: ON HSC Fc 10000.0 Hz Gain +3.5 dB Q 0.70
        """
        preset = parse_equalizer_apo(apo_text, default_name="AutoEQ Sennheiser")
        self.assertIsNotNone(preset)
        self.assertEqual(preset.name, "AutoEQ Sennheiser")
        self.assertAlmostEqual(preset.preamp_db, -5.5)
        self.assertEqual(len(preset.bands), 3)

        b1 = preset.bands[0]
        self.assertEqual(b1.filter_type, FilterType.PEAKING)
        self.assertAlmostEqual(b1.frequency, 65.0)
        self.assertAlmostEqual(b1.gain_db, 4.5)
        self.assertAlmostEqual(b1.q, 0.70)
        self.assertTrue(b1.enabled)

        b2 = preset.bands[1]
        self.assertEqual(b2.filter_type, FilterType.LOW_SHELF)
        self.assertAlmostEqual(b2.frequency, 105.0)
        self.assertAlmostEqual(b2.gain_db, -2.1)
        self.assertAlmostEqual(b2.q, 0.71)
        self.assertTrue(b2.enabled)

        b3 = preset.bands[2]
        self.assertEqual(b3.filter_type, FilterType.HIGH_SHELF)
        self.assertAlmostEqual(b3.frequency, 10000.0)
        self.assertAlmostEqual(b3.gain_db, 3.5)
        self.assertAlmostEqual(b3.q, 0.70)
        self.assertTrue(b3.enabled)

    def test_parse_peace_format_and_variations(self):
        peace_text = """
        # Profile: Moondrop Blessing 2 Dusk
        ; Auto-generated EqualizerAPO config
        Preamp: -4.2
        ON PK Fc 1000 Gain -1.5 Q 1.8
        OFF LSC Fc 80 Gain 3.0 Q 0.71
        Filter 3: ON HSC Fc 12000 Hz Gain -2.0 dB Q 1.0
        """
        preset = parse_equalizer_apo(peace_text)
        self.assertIsNotNone(preset)
        self.assertEqual(preset.name, "Moondrop Blessing 2 Dusk")
        self.assertAlmostEqual(preset.preamp_db, -4.2)
        self.assertEqual(len(preset.bands), 3)

        self.assertTrue(preset.bands[0].enabled)
        self.assertFalse(preset.bands[1].enabled)
        self.assertTrue(preset.bands[2].enabled)
        self.assertEqual(preset.bands[1].filter_type, FilterType.LOW_SHELF)
        self.assertEqual(preset.bands[2].filter_type, FilterType.HIGH_SHELF)

    def test_parse_empty_and_invalid(self):
        self.assertIsNone(parse_equalizer_apo(""))
        self.assertIsNone(parse_equalizer_apo("   \n\t  "))
        self.assertIsNone(parse_equalizer_apo("# Just a comment\n; Another comment"))
        self.assertIsNone(parse_equalizer_apo("Some random text without any filters"))

    def test_parse_preamp_positive_and_omitted(self):
        apo_no_preamp = "Filter 1: ON PK Fc 1000 Hz Gain 2.0 dB Q 1.4"
        p1 = parse_equalizer_apo(apo_no_preamp)
        self.assertIsNotNone(p1)
        self.assertAlmostEqual(p1.preamp_db, 0.0)

        apo_pos_preamp = "Preamp: +2.5 dB\nFilter 1: ON PK Fc 1000 Hz Gain -2.0 dB Q 1.4"
        p2 = parse_equalizer_apo(apo_pos_preamp)
        self.assertIsNotNone(p2)
        self.assertAlmostEqual(p2.preamp_db, 2.5)


class TestEQEngineAdvancedSettings(unittest.TestCase):
    def test_precision_setting_and_ffmpeg_af(self):
        engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET, precision="f64")
        self.assertEqual(engine.precision, "f64")
        af_f64 = engine.to_ffmpeg_af()
        self.assertIn(":r=f64", af_f64)
        self.assertNotIn(":r=f32", af_f64)

        engine.set_precision("f32")
        self.assertEqual(engine.precision, "f32")
        af_f32 = engine.to_ffmpeg_af()
        self.assertIn(":r=f32", af_f32)
        self.assertNotIn(":r=f64", af_f32)

        # Invalid precision is ignored
        engine.set_precision("invalid")
        self.assertEqual(engine.precision, "f32")

    def test_auto_headroom_settings_and_guard(self):
        engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET)
        engine.set_auto_headroom(False)
        self.assertFalse(engine.auto_headroom)
        engine.set_auto_headroom(True)
        self.assertTrue(engine.auto_headroom)

        engine.set_headroom_margin(1.5)
        self.assertEqual(engine.headroom_margin, 1.5)

        engine.set_intersample_guard(True)
        self.assertTrue(engine.intersample_guard)
        rec_guard = engine.auto_preamp_headroom()

        engine.set_intersample_guard(False)
        self.assertFalse(engine.intersample_guard)
        rec_no_guard = engine.auto_preamp_headroom()

        self.assertLess(rec_guard, rec_no_guard)

    def test_curve_styles_and_ranges(self):
        engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET)
        engine.set_curve_style("blocks")
        self.assertEqual(engine.curve_style, "blocks")
        engine.set_curve_style("outline")
        self.assertEqual(engine.curve_style, "outline")
        engine.set_curve_style("braille")
        self.assertEqual(engine.curve_style, "braille")
        engine.set_curve_style("unsupported")
        self.assertEqual(engine.curve_style, "braille")

        engine.set_curve_range_db(18.0)
        self.assertEqual(engine.curve_range_db, 18.0)
        engine.set_curve_range_db(24.0)
        self.assertEqual(engine.curve_range_db, 24.0)
        engine.set_curve_range_db(12.0)
        self.assertEqual(engine.curve_range_db, 12.0)
        engine.set_curve_range_db(99.0)  # Invalid, ignored
        self.assertEqual(engine.curve_range_db, 12.0)

    def test_advanced_serialization_roundtrip(self):
        engine = ParametricEQEngine(
            SAMSUNG_AKG_REFERENCE_PRESET,
            sample_rate=96000.0,
            precision="f32",
            auto_headroom=False,
            headroom_margin=1.0,
            intersample_guard=False,
            anti_denormal=False,
            curve_style="blocks",
            curve_range_db=18.0,
        )
        d = engine.to_dict()
        self.assertEqual(d["sample_rate"], 96000.0)
        self.assertEqual(d["precision"], "f32")
        self.assertFalse(d["auto_headroom"])
        self.assertEqual(d["headroom_margin"], 1.0)
        self.assertFalse(d["intersample_guard"])
        self.assertFalse(d["anti_denormal"])
        self.assertEqual(d["curve_style"], "blocks")
        self.assertEqual(d["curve_range_db"], 18.0)

        restored = ParametricEQEngine.from_dict(d)
        self.assertEqual(restored.sample_rate, 96000.0)
        self.assertEqual(restored.precision, "f32")
        self.assertFalse(restored.auto_headroom)
        self.assertEqual(restored.headroom_margin, 1.0)
        self.assertFalse(restored.intersample_guard)
        self.assertFalse(restored.anti_denormal)
        self.assertEqual(restored.curve_style, "blocks")
        self.assertEqual(restored.curve_range_db, 18.0)

    def test_render_curve_dispatchers(self):
        engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET)
        blocks = render_blocks_curve(engine, width=40, height=6)
        self.assertIn("20Hz", blocks)
        self.assertIn("20kHz", blocks)
        self.assertTrue("█" in blocks or "▄" in blocks or "▀" in blocks or "·" in blocks)

        outline = render_outline_curve(engine, width=40, height=6)
        self.assertIn("20Hz", outline)
        self.assertIn("20kHz", outline)
        self.assertIn("●", outline)

        # Dispatcher with style arg
        d_blocks = render_curve(engine, width=40, height=6, style="blocks")
        self.assertEqual(d_blocks, blocks)

        d_outline = render_curve(engine, width=40, height=6, style="outline")
        self.assertEqual(d_outline, outline)

        d_braille = render_curve(engine, width=40, height=6, style="braille")
        self.assertIn("20Hz", d_braille)

    def test_builtin_acoustic_target_presets(self):
        names = [p.name for p in BUILTIN_PRESETS]
        self.assertIn("Samsung AKG Audiophile Pro", names)
        self.assertIn("Samsung AKG Master Reference", names)
        self.assertIn("Harman Target 2019 (In-Ear)", names)
        self.assertIn("Harman Target 2018 (Over-Ear)", names)
        self.assertIn("IEF Neutral 2020", names)
        self.assertIn("Diffuse Field (DF)", names)
        self.assertIn("Free Field (FF)", names)

        for p in BUILTIN_PRESETS:
            self.assertTrue(len(p.bands) > 0)
            self.assertLessEqual(p.preamp_db, 0.0)


class TestEQSettingsModalUI(unittest.TestCase):
    def test_eq_settings_modal_interactions(self):
        import asyncio
        from unittest.mock import patch
        from textual.app import App, ComposeResult
        from spoff.app import EQSettingsModal, EqualizerModal
        from textual.widgets import Static

        class EQSettingsApp(App):
            def __init__(self, engine):
                super().__init__()
                self.eq_engine = engine
                self.player = MPVController(eq_engine=self.eq_engine)

            def compose(self) -> ComposeResult:
                yield Static("Root Screen")

        async def _run():
            engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET)
            app = EQSettingsApp(engine)
            async with app.run_test() as pilot:
                modal = EQSettingsModal(engine)
                app.push_screen(modal)
                await pilot.pause()

                # Test navigation cursor down and up
                modal.action_cursor_down()
                await pilot.pause()
                self.assertEqual(modal.focused.id, "eq-opt-precision")
                modal.action_cursor_up()
                await pilot.pause()
                self.assertEqual(modal.focused.id, "eq-opt-sample-rate")

                # Test switch focus (Tab / Shift+Tab)
                modal.action_switch_focus()
                await pilot.pause()
                self.assertEqual(modal.focused.id, "eq-opt-precision")
                modal.action_switch_focus_back()
                await pilot.pause()
                self.assertEqual(modal.focused.id, "eq-opt-sample-rate")

                # Test action_select_or_toggle when focused on sample rate
                modal.action_select_or_toggle()
                self.assertEqual(engine.sample_rate, 88200.0)

                # Test cycle sample rate
                modal.cycle_sample_rate()
                self.assertEqual(engine.sample_rate, 96000.0)

                # Test cycle precision
                modal.cycle_precision()
                self.assertEqual(engine.precision, "f32")
                modal.cycle_precision()
                self.assertEqual(engine.precision, "f64")

                # Test toggle anti-denormal
                modal.toggle_anti_denormal()
                self.assertFalse(engine.anti_denormal)
                modal.toggle_anti_denormal()
                self.assertTrue(engine.anti_denormal)

                # Test toggle auto headroom
                modal.toggle_auto_headroom()
                self.assertFalse(engine.auto_headroom)
                modal.toggle_auto_headroom()
                self.assertTrue(engine.auto_headroom)

                # Test cycle headroom margin
                modal.cycle_headroom_margin()
                self.assertEqual(engine.headroom_margin, 1.0)

                # Test toggle intersample guard
                modal.toggle_intersample_guard()
                self.assertFalse(engine.intersample_guard)
                modal.toggle_intersample_guard()
                self.assertTrue(engine.intersample_guard)

                # Test cycle curve style
                modal.cycle_curve_style()
                self.assertEqual(engine.curve_style, "blocks")

                # Test cycle curve range
                modal.cycle_curve_range()
                self.assertEqual(engine.curve_range_db, 18.0)

                # Test cycle target profile
                orig_name = engine.preset_name
                modal.cycle_target_profile()
                self.assertNotEqual(engine.preset_name, orig_name)

                # Test reset to reference
                modal.reset_to_reference()
                self.assertEqual(engine.preset_name, "Samsung AKG Master Reference")

                # Test clipboard export
                with patch("spoff.app.copy_to_clipboard", return_value=True) as mock_copy:
                    modal.export_to_clipboard()
                    mock_copy.assert_called_once()

                # Test clipboard import with auto_headroom active (attenuates to prevent 0 dBFS clipping)
                sample_apo = "Preamp: -3.0 dB\nFilter 1: ON PK Fc 1000.0 Hz Gain 4.0 dB Q 1.0"
                with patch("spoff.app.read_from_clipboard", return_value=sample_apo):
                    modal.import_from_clipboard()
                    self.assertEqual(len(engine.bands), 1)
                    # Auto-headroom calculates -5.2 dB (4.0 dB peak + 1.0 margin + 0.2 intersample guard)
                    self.assertLess(engine.preamp_db, -4.0)

                    # When auto_headroom is disabled, imported preamp is retained as-is (-3.0 dB)
                    engine.set_auto_headroom(False)
                    modal.import_from_clipboard()
                    self.assertAlmostEqual(engine.preamp_db, -3.0)

                # Test open live editor
                modal.open_live_editor()
                await pilot.pause()
                self.assertIsInstance(app.screen, EqualizerModal)

        asyncio.run(_run())

    def test_open_eq_settings_from_equalizer_modal(self):
        import asyncio
        from textual.app import App, ComposeResult
        from spoff.app import EqualizerModal, EQSettingsModal
        from textual.widgets import Static

        class EQApp(App):
            def __init__(self, engine):
                super().__init__()
                self.eq_engine = engine
                self.player = MPVController(eq_engine=self.eq_engine)

            def compose(self) -> ComposeResult:
                yield Static("Root Screen")

        async def _run():
            engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET)
            app = EQApp(engine)
            async with app.run_test() as pilot:
                eq_modal = EqualizerModal(engine)
                app.push_screen(eq_modal)
                await pilot.pause()

                # Trigger open settings action (bound to s/S)
                eq_modal.action_open_settings()
                await pilot.pause()

                self.assertIsInstance(app.screen, EQSettingsModal)
                app.screen.action_dismiss_modal()
                await pilot.pause()
                self.assertIsInstance(app.screen, EqualizerModal)

        asyncio.run(_run())

    def test_open_eq_settings_from_settings_modal(self):
        import asyncio
        from textual.app import App, ComposeResult
        from spoff.app import SettingsModal, EQSettingsModal, DEFAULT_KEYBINDINGS
        from textual.widgets import Static

        class SettingsApp(App):
            def __init__(self, engine):
                super().__init__()
                self.eq_engine = engine
                self.player = MPVController(eq_engine=self.eq_engine)
                self.keybindings = dict(DEFAULT_KEYBINDINGS)

            def compose(self) -> ComposeResult:
                yield Static("Root Screen")

            def reset_all_keybindings(self):
                pass

        async def _run():
            engine = ParametricEQEngine(SAMSUNG_AKG_REFERENCE_PRESET)
            app = SettingsApp(engine)
            async with app.run_test() as pilot:
                settings_modal = SettingsModal()
                app.push_screen(settings_modal)
                await pilot.pause()

                # Trigger open_eq_settings from SettingsModal
                settings_modal.open_eq_settings()
                await pilot.pause()

                self.assertIsInstance(app.screen, EQSettingsModal)
                app.screen.action_dismiss_modal()
                await pilot.pause()
                self.assertIsInstance(app.screen, SettingsModal)

        asyncio.run(_run())


class TestDirectHardwareAudioDevice(unittest.TestCase):
    def test_detect_direct_hardware_sink_when_filter_sink_present(self):
        from unittest.mock import patch, MagicMock
        from spoff.player import get_direct_hardware_audio_device

        mock_stdout = (
            "41\tsamsung_akg_eq\tPipeWire\tfloat32le 2ch 48000Hz\tRUNNING\n"
            "68\talsa_output.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts32le 2ch 48000Hz\tRUNNING\n"
        )
        mock_proc = MagicMock(returncode=0, stdout=mock_stdout)
        with patch("subprocess.run", return_value=mock_proc):
            dev = get_direct_hardware_audio_device()
            self.assertEqual(dev, "pulse/alsa_output.pci-0000_00_1f.3.analog-stereo")

    def test_direct_hardware_sink_returns_none_when_no_filter_sink(self):
        from unittest.mock import patch, MagicMock
        from spoff.player import get_direct_hardware_audio_device

        # Only ALSA output present, no virtual software filters
        mock_stdout = "68\talsa_output.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts32le 2ch 48000Hz\tRUNNING\n"
        mock_proc = MagicMock(returncode=0, stdout=mock_stdout)
        with patch("subprocess.run", return_value=mock_proc):
            dev = get_direct_hardware_audio_device()
            self.assertIsNone(dev)

    def test_direct_hardware_sink_handles_failure(self):
        from unittest.mock import patch
        from spoff.player import get_direct_hardware_audio_device

        with patch("subprocess.run", side_effect=Exception("pactl failed")):
            dev = get_direct_hardware_audio_device()
            self.assertIsNone(dev)


if __name__ == "__main__":
    unittest.main()


