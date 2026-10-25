import { useMemo, useRef, useState } from 'react';
import { Pressable, StyleSheet, Switch, Text, View } from 'react-native';
import type { EffectConfig, EffectStatus } from '@lighttime/sdk';
import { createEffectForm, EFFECT_CHOICES, effectConfigFromForm, supportsUntilStopped } from './effectForm';
import type { EffectChoiceId, EffectForm } from './effectForm';
import { groupIndexOf, SCENE_LOOKS, SCENE_ORDER } from './pattern-ui/catalog';
import { KeyframeEditor } from './pattern-ui/KeyframeEditor';
import { glass, jelly, ObsidianButton, PlusGlyph, StopGlyph } from './pattern-ui/materials';
import { PatternBrowser } from './pattern-ui/PatternBrowser';
import { ScenePreview } from './pattern-ui/ScenePreview';
import { Chip, Field, Range } from './pattern-ui/TuningControls';
import { colors as c, type as t } from './pattern-ui/palette';
import { formatSeconds, previewEffect } from './pattern-ui/preview';
import { draftsFromFrames, keyframesFromDrafts } from './pattern-ui/keyframes';
import type { FrameDraft } from './pattern-ui/keyframes';

const titleOf = (id: EffectChoiceId) => EFFECT_CHOICES.find(item => item.id === id)!.title;

export function PatternPanel({ status, ready, working, connected, onStart, onStop }: {
  status: EffectStatus; ready: boolean; working: boolean; connected: boolean;
  onStart: (config: EffectConfig) => Promise<void>; onStop: () => Promise<void>;
}) {
  const [form, setForm] = useState(() => createEffectForm('candle'));
  const [frameDrafts, setFrameDrafts] = useState<FrameDraft[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);
  const [groupIndex, setGroupIndex] = useState(() => groupIndexOf('candle'));
  const startInFlight = useRef(false);
  const [startAttempt, setStartAttempt] = useState<{
    previousId: string | null; title: string; choice: EffectChoiceId; pattern: EffectConfig['pattern']; settled: boolean;
  } | null>(null);
  const [startedScene, setStartedScene] = useState<{ id: string; title: string; choice: EffectChoiceId } | null>(null);
  const change = <K extends keyof EffectForm>(key: K, value: EffectForm[K]) => setForm(previous => ({ ...previous, [key]: value }));
  const choice = EFFECT_CHOICES.find(item => item.id === form.id)!;
  const pattern = form.base.pattern;
  const finiteOnly = !supportsUntilStopped(form);
  const keyframes = form.base.pattern === 'keyframes' ? form.base.keyframes : null;
  const hasPeriod = ['blink', 'breathe', 'cct-sweep'].includes(pattern);
  const hasEnd = ['flicker', 'sunrise', 'emergency', 'tv', 'cct-sweep'].includes(pattern);
  const hasSeed = ['flicker', 'lightning', 'paparazzi', 'tv'].includes(pattern);
  const hasCurve = ['breathe', 'fade-in', 'fade-out', 'sunrise'].includes(pattern);
  const hasBaselineCct = useMemo(() => createEffectForm(form.id).cct === '', [form.id]);
  const requested = useMemo(() => {
    try {
      const config = effectConfigFromForm(form);
      const result = previewEffect(config);
      return { config: result.preview ? config : null, ...result };
    } catch (error) {
      return { config: null, preview: null, error: error instanceof Error ? error.message : 'Check the requested settings.' };
    }
  }, [form]);
  const progress = status.progress === null ? null : Math.round(status.progress * 100);
  const startDisabled = !ready || !requested.config;
  const stopDisabled = !connected || working || !status.running;
  const note = pattern === 'keyframes' ? 'Your sequence of brightness and white temperature.'
    : pattern === 'sunrise' && form.base.pattern === 'sunrise' && form.base.direction !== 'set'
      ? 'A gradual journey from warm white to daylight.' : choice.note;
  // A resolved callback can also mean failure. Bind a title only when the runner
  // publishes a new accepted run, independently of subsequent draft selection.
  const accepted = startAttempt && status.running && status.id && status.id !== startAttempt.previousId
    && status.pattern === startAttempt.pattern && !status.lastError;
  const runningTitle = accepted ? startAttempt.title : startedScene?.id === status.id ? startedScene.title : status.pattern;
  const runningChoice = !status.running ? null : accepted ? startAttempt.choice
    : startedScene?.id === status.id ? startedScene.choice : null;
  // Without a bound title the runner reports its SDK pattern; show it as a readable name.
  const runningName = runningTitle && runningTitle === status.pattern
    ? `${runningTitle.charAt(0).toUpperCase()}${runningTitle.slice(1).replace(/-/g, ' ')}` : runningTitle;
  if (accepted) {
    setStartedScene({ id: status.id!, title: startAttempt.title, choice: startAttempt.choice });
    setStartAttempt(null);
  } else if (startAttempt?.settled) setStartAttempt(null);

  const start = async () => {
    if (!requested.config || startInFlight.current) return;
    startInFlight.current = true;
    const attempt = { previousId: status.id, title: choice.title, choice: form.id, pattern: requested.config.pattern, settled: false };
    setStartAttempt(attempt);
    try { await onStart(requested.config); }
    catch { /* The owner supplies the actual runner status and error. */ }
    finally {
      startInFlight.current = false;
      setStartAttempt(current => current === attempt ? { ...attempt, settled: true } : current);
    }
  };

  // Selecting a scene loads its preset as a draft; the light changes only on Start.
  const select = (id: EffectChoiceId, follow: boolean) => {
    if (follow) setGroupIndex(groupIndexOf(id));
    if (id !== form.id) {
      const next = createEffectForm(id);
      setForm(next);
      setFrameDrafts(next.base.pattern === 'keyframes' ? draftsFromFrames(next.base.keyframes) : []);
    }
  };
  const position = SCENE_ORDER.indexOf(form.id);
  const previousId = SCENE_ORDER[(position + SCENE_ORDER.length - 1) % SCENE_ORDER.length]!;
  const nextId = SCENE_ORDER[(position + 1) % SCENE_ORDER.length]!;
  const live = runningChoice === form.id;
  const subtitle = live ? 'Running now' : status.running ? `Start replaces ${runningName}` : SCENE_LOOKS[form.id].mood;
  const requestedWhite = requested.config?.pattern === 'keyframes' ? 'White by frame'
    : requested.config?.cct == null ? 'White at Start'
      : hasEnd && requested.config.cctEnd != null && requested.config.cctEnd !== requested.config.cct
        ? `${Math.min(requested.config.cct, requested.config.cctEnd)}–${Math.max(requested.config.cct, requested.config.cctEnd)} K`
        : `${requested.config.cct} K`;
  const summary = requested.preview
    ? `${requested.preview.brightnessLabel} · ${requestedWhite} · ${requested.preview.runLabel}` : null;
  const stateLabel = status.running ? `Running · ${runningName}` : status.id
    ? `${status.lastError ? 'Stopped with an error' : progress === 100 ? 'Finished' : 'Stopped'} · ${runningName}` : null;
  const readyHint = ready ? null : !connected ? 'Connect your G60 to start a scene.'
    : working ? 'Finishing the current light operation…' : 'Read the light to verify its output before starting.';

  return <View style={s.panel}>
    <View pointerEvents="none" style={s.backdrop} />
    <ScenePreview id={form.id} title={choice.title} preview={requested.preview} subtitle={subtitle} summary={summary} live={live}
      startDisabled={startDisabled} onStart={() => { void start(); }}
      startLabel={live ? `Start ${choice.title} again, replacing the running pattern`
        : status.running ? `Start ${choice.title}, replacing the running pattern` : `Start ${choice.title}`}
      startHint={`Apply the ${choice.title} draft to your light. A new run replaces the current pattern.`}
      previous={{ title: titleOf(previousId), onPress: () => select(previousId, true) }}
      next={{ title: titleOf(nextId), onPress: () => select(nextId, true) }}
      notesOpen={notesOpen} onNotes={() => setNotesOpen(open => !open)} />

    {notesOpen && <View style={[s.notes, jelly('night', 0.8)]}>
      <Text style={s.notesTitle}>{choice.title}</Text>
      <Text style={s.body}>{note}</Text>
      <Text style={s.hint}>The preview is sampled from the scene’s settings. It is not live output.{requested.preview?.baselineNote ? ` ${requested.preview.baselineNote}` : ''}</Text>
      <Text style={s.hint}>Runs on this phone. Keep Lighttime open: backgrounding or disconnecting stops the pattern and leaves the current settings. Manual brightness, temperature or power changes also stop it.</Text>
    </View>}

    {(readyHint || (stateLabel && !status.running) || progress !== null || status.lastError || requested.error) && <View style={s.status}>
      {readyHint && <View style={s.line}><View style={s.idleDot} /><Text style={s.hintStrong}>{readyHint}</Text></View>}
      {/* While running, the stage and the app's running bar carry the state; afterwards it is stated here. */}
      {((stateLabel && !status.running) || progress !== null) && <View style={s.runner}>
        <View style={s.line}>
          {stateLabel && !status.running && <>
            <View accessible={false} style={[s.dot, !!status.lastError && s.dotError]} />
            <Text accessibilityLiveRegion="polite" style={s.runnerTitle}>{stateLabel}</Text>
          </>}
          {status.running && <Text style={s.runnerTitle}>{runningName}</Text>}
          {progress !== null && <Text style={s.progressText}>{progress}%</Text>}
        </View>
        {progress !== null && <View accessibilityRole="progressbar" accessibilityLabel="Pattern progress"
          accessibilityValue={{ min: 0, max: 100, now: progress }} style={s.progressTrack}>
          <View style={[s.progressFill, { width: `${progress}%` }]} />
          <View style={[s.progressKnob, { left: `${progress}%` }]} />
        </View>}
      </View>}
      {status.lastError && <View style={s.line}><View style={s.alert}><Text style={s.alertMark}>!</Text></View>
        <Text selectable accessibilityLiveRegion="polite" style={s.error}>{status.lastError}</Text></View>}
      {requested.error && <View style={s.line}><View style={s.alert}><Text style={s.alertMark}>!</Text></View>
        <Text accessibilityLiveRegion="polite" style={s.error}>Check tuning · {requested.error}</Text></View>}
    </View>}

    {status.running && <ObsidianButton label="Stop & restore" glyph={<StopGlyph />} disabled={stopDisabled}
      hint="Stop the running pattern and restore the brightness and temperature read when it started." onPress={() => { void onStop(); }} />}

    <PatternBrowser groupIndex={groupIndex} onGroup={setGroupIndex} selected={form.id} running={runningChoice}
      preview={requested.preview} onSelect={id => select(id, false)} />

    <Pressable accessibilityRole="button" accessibilityLabel="Tune pattern" accessibilityState={{ expanded }}
      accessibilityHint={keyframes ? 'Edit frames, easing and run time.' : 'Adjust brightness, white temperature and timing.'}
      onPress={() => setExpanded(!expanded)} style={({ pressed }) => [s.tuneToggle, glass, pressed && s.pressed]}>
      <Text style={s.tuneLabel}>Tune</Text>
      <Text numberOfLines={1} style={s.tuneHint}>{keyframes ? 'Frames · easing · run time' : 'Brightness · white · timing'}</Text>
      <View style={s.tuneOrb}><PlusGlyph open={expanded} /></View>
    </Pressable>
    {expanded && <View style={[s.tuning, jelly('night', 0.8)]}>
      {keyframes ? <KeyframeEditor drafts={frameDrafts} onChange={drafts => {
        setFrameDrafts(drafts);
        setForm(previous => previous.base.pattern === 'keyframes'
          ? { ...previous, base: { ...previous.base, keyframes: keyframesFromDrafts(drafts) } } : previous);
      }} /> : <>
        <Range label="Minimum brightness" value={form.minBrightness} min={0} max={100} step={1} unit="%" onChange={value => change('minBrightness', value)} />
        <Range label="Maximum brightness" value={form.maxBrightness} min={0} max={100} step={1} unit="%" onChange={value => change('maxBrightness', value)} />
        <Range label={hasEnd ? 'Start temperature' : 'Color temperature'} value={form.cct} min={2700} max={6500} step={100} unit="K" onChange={value => change('cct', value)} />
        {!form.cct && <Text style={s.hint}>Uses the verified temperature captured when Start is pressed.</Text>}
        {hasBaselineCct && <Chip label="Use temperature at Start" selected={form.cct === ''} onPress={() => change('cct', '')} />}
        {hasEnd && <Range label="End temperature" value={form.cctEnd} min={2700} max={6500} step={100} unit="K" onChange={value => change('cctEnd', value)} />}
      </>}

      <View style={s.rule} />
      <Text style={s.fieldLabel}>Run time</Text>
      <View style={s.chips}>
        {!finiteOnly && <Chip label="Until stopped" selected={form.untilStopped} onPress={() => change('untilStopped', true)} />}
        {[10, 60, 900].map(seconds => <Chip key={seconds} label={formatSeconds(seconds)}
          selected={!form.untilStopped && form.duration === String(seconds)}
          onPress={() => setForm(previous => ({ ...previous, duration: String(seconds), untilStopped: false }))} />)}
      </View>
      {!form.untilStopped && <Field label="Duration · s" value={form.duration} onChange={value => change('duration', value)} hint="1–3600 s" />}
      {hasPeriod && <Field label="Period · s" value={form.period} onChange={value => change('period', value)}
        hint={pattern === 'blink' ? '0.2–60 s per on/off cycle; each phase at least 0.1 s' : '1–60 s per cycle'} />}
      {form.base.pattern === 'blink' && <Range label="On time" value={String(Math.round((form.base.duty ?? 0.5) * 100))}
        min={0} max={100} step={1} unit="%" onChange={value => setForm(previous => previous.base.pattern === 'blink'
          ? { ...previous, base: { ...previous.base, duty: Number(value) / 100 } } : previous)} />}
      {hasCurve && <View style={s.optionGroup}>
        <Text style={s.fieldLabel}>Brightness curve</Text>
        <View style={s.chips}>{(['linear', 'perceptual'] as const).map(curve => <Chip key={curve}
          label={curve === 'linear' ? 'Linear' : 'Perceptual'} selected={(form.base.curve ?? (pattern === 'sunrise' ? 'perceptual' : 'linear')) === curve}
          onPress={() => setForm(previous => ({ ...previous, base: { ...previous.base, curve } }))} />)}</View>
        <Text style={s.hint}>Perceptual spends longer at soft levels.</Text>
      </View>}
      {pattern === 'pulse' && <Field label="Pulse · BPM" value={form.bpm} onChange={value => change('bpm', value)} hint="20–120 BPM" />}
      {pattern === 'morse' && <>
        <Field label="Morse message" text value={form.text} onChange={value => change('text', value)} hint="Up to 64 letters, digits, spaces or . , ? / = + - @" />
        <Field label="Morse dot · s" value={form.unit} onChange={value => change('unit', value)} hint="0.1–2 s per dot" />
      </>}
      {hasSeed && <Field label="Random seed" value={form.seed} onChange={value => change('seed', value)} hint="0–4294967295 · the same seed repeats the sequence" />}
      {!form.untilStopped && <View style={s.switchRow}>
        <View style={s.switchText}><Text style={s.fieldLabel}>Restore when finished</Text><Text style={s.hint}>Return to both settings captured at Start.</Text></View>
        <Switch accessibilityLabel="Restore baseline on completion" value={form.restoreOnComplete} onValueChange={value => change('restoreOnComplete', value)}
          hitSlop={8} trackColor={{ false: '#4A1416', true: '#D8262E' }} thumbColor={c.white} style={s.switch} />
      </View>}
    </View>}
  </View>;
}

const s = StyleSheet.create({
  panel: { paddingTop: 4, paddingBottom: 24, gap: 18 },
  backdrop: { position: 'absolute', top: 0, left: -70, right: -70, height: 720,
    backgroundImage: 'radial-gradient(ellipse 58% 50% at 50% 42%, rgba(223, 33, 33, 0.62) 0%, rgba(194, 36, 34, 0.34) 42%, rgba(97, 11, 10, 0) 100%)' },
  notes: { borderRadius: 30, padding: 18, gap: 8 },
  notesTitle: { color: c.white, fontFamily: t.medium, fontSize: 18, lineHeight: 26 },
  body: { color: c.pink, fontFamily: t.regular, fontSize: 15, lineHeight: 22 },
  hint: { color: c.soft, fontFamily: t.regular, fontSize: 13, lineHeight: 19 },
  hintStrong: { color: c.pink, fontFamily: t.regular, fontSize: 14, lineHeight: 20, flexShrink: 1 },
  status: { gap: 10, paddingHorizontal: 6 },
  line: { flexDirection: 'row', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  runner: { gap: 10 },
  runnerTitle: { color: c.white, fontFamily: t.medium, fontSize: 15, lineHeight: 22, flexShrink: 1, flexGrow: 1 },
  idleDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: c.soft },
  dot: { width: 9, height: 9, borderRadius: 5, backgroundColor: c.soft },
  dotLive: { backgroundColor: c.live, boxShadow: '0 0 10px rgba(255, 90, 80, 0.9)' },
  dotError: { backgroundColor: c.peach },
  progressText: { color: c.white, fontFamily: t.medium, fontSize: 15, lineHeight: 22, fontVariant: ['tabular-nums'] },
  progressTrack: { height: 6, borderRadius: 3, backgroundColor: 'rgba(255, 210, 210, 0.2)', justifyContent: 'center' },
  progressFill: { height: 6, borderRadius: 3, backgroundColor: c.white },
  progressKnob: { position: 'absolute', width: 16, height: 16, marginLeft: -8, borderRadius: 8, backgroundColor: c.white,
    boxShadow: '0 0 10px rgba(255, 220, 220, 0.85)' },
  alert: { width: 24, height: 24, borderRadius: 12, borderWidth: 2, borderColor: c.peach, alignItems: 'center', justifyContent: 'center' },
  alertMark: { color: c.peach, fontFamily: t.semibold, fontSize: 13, lineHeight: 16, includeFontPadding: false },
  error: { color: c.peach, fontFamily: t.regular, fontSize: 14, lineHeight: 21, flexShrink: 1, flexBasis: 200, flexGrow: 1 },
  tuneToggle: { minHeight: 64, borderRadius: 32, paddingLeft: 22, paddingRight: 10, paddingVertical: 8, flexDirection: 'row',
    alignItems: 'center', gap: 12 },
  tuneLabel: { color: c.white, fontFamily: t.medium, fontSize: 17, lineHeight: 24 },
  tuneHint: { flex: 1, color: c.soft, fontFamily: t.regular, fontSize: 13, lineHeight: 19 },
  tuneOrb: { width: 44, height: 44, borderRadius: 22, alignItems: 'center', justifyContent: 'center', backgroundColor: '#C81E2A',
    boxShadow: 'inset 0 1.5px 0 rgba(255, 228, 228, 0.7), inset 0 -5px 10px -4px rgba(255, 190, 190, 0.5), 0 0 16px rgba(223, 33, 33, 0.6)' },
  tuning: { borderRadius: 32, padding: 18, gap: 22 },
  fieldLabel: { color: c.white, fontFamily: t.medium, fontSize: 15, lineHeight: 22 },
  rule: { height: 1, backgroundColor: 'rgba(255, 190, 190, 0.18)' }, optionGroup: { gap: 12 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  switchRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 12 }, switchText: { flexGrow: 1, flexBasis: 180, gap: 4 },
  switch: { minHeight: 48, minWidth: 48 },
  pressed: { opacity: 0.8 },
});
