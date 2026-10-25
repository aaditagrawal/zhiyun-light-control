import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AccessibilityInfo, ActivityIndicator, AppState, Modal, Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { WebView } from 'react-native-webview';
import type { WebViewMessageEvent } from 'react-native-webview';
import { G60Artwork } from '../ui/G60Artwork';
import { color, font } from '../ui/theme';
import { glass, ObsidianButton, StopGlyph } from '../pattern-ui/materials';
import { isShowcaseNavigation, parseShowcaseEvent, showcaseCommandScript, SHOWCASE_LOAD_PHASES, SHOWCASE_ASSET_URL } from './bridge';
import { ShowcaseLoadBudget } from './loading';
import type { ShowcaseCommand } from './bridge';

/** A local product demo. Its bridge has no connection to Bluetooth or output settings. */
export default function G60Showcase({ onClose, running, working, sceneError, onStop }: {
  onClose: () => void; running: boolean; working: boolean; sceneError: string | null; onStop: () => Promise<unknown>;
}) {
  const { fontScale } = useWindowDimensions();
  const viewer = useRef<WebView<object>>(null);
  const currentAttempt = useRef(0);
  const accepting = useRef(true);
  const [attempt, setAttempt] = useState(0);
  const [active, setActive] = useState(AppState.currentState === 'active');
  const [reducedMotion, setReducedMotion] = useState<boolean | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [spinning, setSpinning] = useState(false);
  const [interacted, setInteracted] = useState(false);
  const [viewLabel, setViewLabel] = useState('Three-quarter view');
  const [loadBudget] = useState(() => new ShowcaseLoadBudget());
  const loadPhase = useRef(-1);
  const [loadProgress, setLoadProgress] = useState(-1);
  const previousSceneError = useRef(sceneError);
  const restoring = useRef(false);
  const [restorePending, setRestorePending] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(running ? sceneError : null);
  const error = restoreError ?? sessionError;
  const command = useCallback((action: ShowcaseCommand) => {
    viewer.current?.injectJavaScript(showcaseCommandScript(action));
  }, []);
  const close = useCallback(() => { accepting.current = false; command('dispose'); onClose(); }, [command, onClose]);
  const source = useMemo(() => reducedMotion === null ? null : {
    uri: SHOWCASE_ASSET_URL + (reducedMotion ? '#reduce-motion' : ''),
  }, [reducedMotion]);
  const report = () => viewer.current?.injectJavaScript(`(()=>{const state=window.G60Showcase?.getState();window.ReactNativeWebView?.postMessage(JSON.stringify({type:'diagnostic',phase:'document',ready:state?.ready,quality:state?.quality,renderer:state?.renderer,loadMs:window.G60Showcase?.loadMs,visibility:document.visibilityState}));})();true;`);

  useEffect(() => {
    let mounted = true;
    void AccessibilityInfo.isReduceMotionEnabled().then(value => { if (mounted) setReducedMotion(value); })
      .catch(() => { if (mounted) setReducedMotion(true); });
    return () => { mounted = false; };
  }, []);
  useEffect(() => {
    if (sceneError !== previousSceneError.current) {
      previousSceneError.current = sceneError; setSessionError(sceneError);
    }
  }, [sceneError]);

  useEffect(() => {
    const listener = AppState.addEventListener('change', next => {
      setActive(next === 'active'); command(next === 'active' ? 'resume' : 'pause');
    });
    return () => { listener.remove(); command('dispose'); };
  }, [command]);
  useEffect(() => {
    if (status !== 'loading' || !active || !source) return;
    loadBudget.resume();
    const timeout = setTimeout(() => { accepting.current = false; setStatus('error'); }, loadBudget.remaining());
    return () => { clearTimeout(timeout); loadBudget.pause(); };
  }, [status, attempt, active, source, loadBudget, loadProgress]);

  const fail = (fromAttempt: number) => {
    if (fromAttempt !== currentAttempt.current || !accepting.current) return;
    accepting.current = false; setStatus('error');
  };
  const message = (event: WebViewMessageEvent, fromAttempt: number) => {
    if (fromAttempt !== currentAttempt.current || !accepting.current) return;
    if (event.nativeEvent.data.length <= 1024) console.info('G60 page', event.nativeEvent.data);
    const data = parseShowcaseEvent(event.nativeEvent.data);
    if (data?.type === 'progress') {
      const phase = SHOWCASE_LOAD_PHASES.indexOf(data.phase);
      if (phase > loadPhase.current) {
        loadPhase.current = phase; loadBudget.progress(); setLoadProgress(phase);
      }
    } else if (data?.type === 'ready') {
      setStatus('ready');
      command(AppState.currentState === 'active' ? 'resume' : 'pause');
      report();
    } else if (data?.type === 'error') fail(fromAttempt);
    else if (data?.type === 'state') setSpinning(data.spinning);
    else if (data?.type === 'interaction') { setInteracted(true); setViewLabel('Free orbit'); }
  };
  const preset = (action: 'front' | 'back' | 'reset') => {
    setInteracted(true);
    setViewLabel(action === 'front' ? 'Front view' : action === 'back' ? 'Rear view' : 'Three-quarter view');
    command(action);
  };
  const retry = () => {
    currentAttempt.current += 1; accepting.current = true;
    loadBudget.reset(); loadPhase.current = -1; setLoadProgress(-1);
    setStatus('loading'); setInteracted(false); setSpinning(false); setViewLabel('Three-quarter view');
    setAttempt(currentAttempt.current);
  };
  const restore = async () => {
    if (restoring.current) return;
    restoring.current = true; setRestorePending(true); setRestoreError(null);
    try { await onStop(); }
    catch (cause) { setRestoreError(cause instanceof Error ? cause.message : 'The light could not be restored.'); }
    finally { restoring.current = false; setRestorePending(false); }
  };

  return <Modal visible animationType="fade" statusBarTranslucent navigationBarTranslucent
    presentationStyle="fullScreen" onRequestClose={close}>
    <SafeAreaView style={s.screen}>
      <StatusBar style="light" />
      <View style={s.header}>
        <Pressable accessibilityRole="button" accessibilityLabel="Close 3D showcase" onPress={close}
          style={({ pressed }) => [s.round, glass, pressed && s.pressed]}>
          <View accessible={false} style={s.closeGlyph}><View style={[s.closeLine, s.slantLeft]} /><View style={[s.closeLine, s.slantRight]} /></View>
        </Pressable>
        <Text accessibilityRole="header" maxFontSizeMultiplier={1.3} style={s.title}>MOLUS G60</Text>
        <Pressable accessibilityRole="button" accessibilityLabel={spinning ? 'Pause model spin' : 'Spin model'}
          accessibilityState={{ selected: spinning, disabled: status !== 'ready' }} disabled={status !== 'ready'}
          onPress={() => { setInteracted(true); command('spin'); }}
          style={({ pressed }) => [s.round, glass, spinning && s.selected, status !== 'ready' && s.disabled, pressed && s.pressed]}>
          {spinning ? <View accessible={false} style={s.pauseGlyph}><View style={s.pauseLine} /><View style={s.pauseLine} /></View>
            : <View accessible={false} style={s.spinGlyph}><View style={s.spinArrow} /></View>}
        </Pressable>
      </View>
      {(running || restorePending || error) && <View style={s.running}>
        {(running || restorePending) && <ObsidianButton label={restorePending ? 'Restoring…' : 'Stop & restore'} glyph={<StopGlyph />} disabled={working || restorePending} onPress={() => { void restore(); }}
          hint="Stop the running scene and restore its captured settings." />}
        {error && <ScrollView style={s.errorBox} contentContainerStyle={s.errorContent}>
          <Text accessibilityLiveRegion="polite" style={s.error}>{error}</Text>
        </ScrollView>}
      </View>}
      <View style={s.stage}>
        {status !== 'error' && source && <WebView<object> key={attempt} ref={viewer} source={source}
          style={s.webview} containerStyle={s.webContainer}
          accessible={false} importantForAccessibility="no-hide-descendants"
          originWhitelist={['*']} onShouldStartLoadWithRequest={request => isShowcaseNavigation(request.url)}
          onMessage={event => message(event, attempt)} onError={() => fail(attempt)} onRenderProcessGone={() => fail(attempt)}
          onLoadEnd={report}
          javaScriptEnabled domStorageEnabled={false} allowFileAccess={false}
          allowFileAccessFromFileURLs={false} allowUniversalAccessFromFileURLs={false}
          mixedContentMode="never" setSupportMultipleWindows={false}
          scrollEnabled={false} bounces={false} overScrollMode="never" />}
        {status !== 'ready' && <View pointerEvents="none" style={s.fallback}><G60Artwork /></View>}
        <View pointerEvents="none" style={s.topFade} /><View pointerEvents="none" style={s.bottomFade} />
        <View pointerEvents="none" accessible accessibilityRole="image"
          accessibilityLabel={`Illustrative G60 3D model. ${status === 'loading' ? 'Loading' : status === 'error' ? 'Unavailable' : spinning ? 'Rotating view' : viewLabel}. Use the Front, Back and Reset buttons to change the view.`}
          style={s.accessibleModel} />
        {status === 'loading' && <View pointerEvents="none" style={s.loading}>
          <ActivityIndicator color={color.text} /><Text style={s.hint}>Loading model</Text>
        </View>}
        {status === 'error' && <View style={s.failure}>
          <Text accessibilityLiveRegion="polite" style={s.hint}>3D couldn’t start on this device.</Text>
          <ObsidianButton label="Try again" onPress={retry} />
        </View>}
      </View>
      <ScrollView style={s.footerScroll} contentContainerStyle={s.footer}>
        <Text style={s.hint}>{status === 'ready' && !interacted ? 'Drag to rotate · Pinch to zoom' : 'Model preview'}</Text>
        <View style={[s.controls, fontScale > 1.3 && s.largeControls]}>
          {(['front', 'back', 'reset'] as const).map(action => <Pressable key={action} accessibilityRole="button"
            accessibilityLabel={action === 'reset' ? 'Reset model view' : `Show ${action} of G60`}
            accessibilityState={{ disabled: status !== 'ready' }} disabled={status !== 'ready'} onPress={() => preset(action)}
            style={({ pressed }) => [s.control, fontScale > 1.3 && s.largeControl, glass, status !== 'ready' && s.disabled, pressed && s.pressed]}>
            <Text style={s.controlLabel}>{action === 'front' ? 'Front' : action === 'back' ? 'Back' : 'Reset'}</Text>
          </Pressable>)}
        </View>
      </ScrollView>
    </SafeAreaView>
  </Modal>;
}

const s = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#150505' },
  header: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 20, paddingVertical: 10 },
  round: { width: 48, height: 48, borderRadius: 24, alignItems: 'center', justifyContent: 'center' },
  closeGlyph: { width: 20, height: 20, justifyContent: 'center', alignItems: 'center' },
  closeLine: { position: 'absolute', width: 22, height: 2, borderRadius: 1, backgroundColor: color.text },
  slantLeft: { transform: [{ rotate: '45deg' }] }, slantRight: { transform: [{ rotate: '-45deg' }] },
  pauseGlyph: { flexDirection: 'row', gap: 5 }, pauseLine: { width: 3, height: 17, borderRadius: 1, backgroundColor: color.text },
  spinGlyph: { width: 21, height: 21, borderWidth: 2, borderColor: color.text, borderRightColor: 'transparent', borderRadius: 11, transform: [{ rotate: '-30deg' }] },
  spinArrow: { position: 'absolute', right: -1, top: -2, width: 7, height: 7, borderTopWidth: 2, borderRightWidth: 2, borderColor: color.text, transform: [{ rotate: '15deg' }] },
  title: { flex: 1, color: color.text, fontFamily: font.medium, fontSize: 18, lineHeight: 26, textAlign: 'center' },
  selected: { borderWidth: 1, borderColor: '#FFB2B0' },
  running: { paddingHorizontal: 20, paddingBottom: 6, gap: 10 },
  errorBox: { maxHeight: 128, flexGrow: 0 }, errorContent: { paddingVertical: 4 },
  error: { color: color.error, fontFamily: font.regular, fontSize: 14, lineHeight: 22 },
  stage: { flex: 1, minHeight: 0 },
  fallback: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, justifyContent: 'center', paddingHorizontal: 12 },
  webContainer: { flex: 1, backgroundColor: 'transparent' },
  webview: { flex: 1, backgroundColor: 'transparent' },
  topFade: { position: 'absolute', top: 0, left: 0, right: 0, height: 80,
    backgroundImage: 'linear-gradient(180deg, rgba(21,5,5,1) 0%, rgba(21,5,5,0.8) 25%, rgba(21,5,5,0.3) 60%, rgba(21,5,5,0) 100%)' },
  bottomFade: { position: 'absolute', bottom: 0, left: 0, right: 0, height: 60,
    backgroundImage: 'linear-gradient(0deg, rgba(21,5,5,1) 0%, rgba(21,5,5,0.8) 25%, rgba(21,5,5,0.3) 60%, rgba(21,5,5,0) 100%)' },
  accessibleModel: { position: 'absolute', top: 4, left: 4, width: 48, height: 48 },
  loading: { position: 'absolute', bottom: 24, alignSelf: 'center', flexDirection: 'row', alignItems: 'center', gap: 10 },
  failure: { position: 'absolute', bottom: 12, left: 20, right: 20, gap: 16 },
  footer: { paddingHorizontal: 20, paddingTop: 12, paddingBottom: 20, gap: 16 },
  footerScroll: { flexGrow: 0, maxHeight: '45%' },
  hint: { color: color.muted, fontFamily: font.regular, fontSize: 13, lineHeight: 20, textAlign: 'center' },
  controls: { flexDirection: 'row', gap: 12, flexWrap: 'wrap' },
  control: { flex: 1, minWidth: 72, minHeight: 52, borderRadius: 26, paddingHorizontal: 12, paddingVertical: 14,
    alignItems: 'center', justifyContent: 'center' },
  controlLabel: { color: color.text, fontFamily: font.medium, fontSize: 14, lineHeight: 22 },
  largeControls: { flexDirection: 'column' },
  largeControl: { flex: 0, minWidth: 0, width: '100%' },
  disabled: { opacity: 0.4 },
  pressed: { opacity: 0.7 },
});
