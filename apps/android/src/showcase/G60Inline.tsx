import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { AccessibilityInfo, Animated, AppState, Pressable, StyleSheet, View } from 'react-native';
import type { GestureResponderEvent } from 'react-native';
import { WebView } from 'react-native-webview';
import type { WebViewMessageEvent } from 'react-native-webview';
import { glass } from '../pattern-ui/materials';
import { color } from '../ui/theme';
import { isShowcaseNavigation, parseShowcaseEvent, showcaseCommandScript, showcaseUri, SHOWCASE_LOAD_PHASES, TouchLock } from './bridge';
import { ShowcaseLoadBudget } from './loading';
import type { ShowcaseCommand } from './bridge';

const LOG = 'G60 inline';

/**
 * The in-place G60 model: a transparent viewer over the native glow, turning slowly by default and
 * dragged or pinched directly. The still artwork stays above it until the renderer is ready, and returns
 * on any failure. It has no connection to Bluetooth or output settings.
 */
export default function G60Inline({ active, still, onInteractionChange }: {
  active: boolean; still: ReactNode; onInteractionChange: (active: boolean) => void;
}) {
  const viewer = useRef<WebView<object>>(null);
  const currentAttempt = useRef(0);
  const accepting = useRef(true);
  const [attempt, setAttempt] = useState(0);
  const [appActive, setAppActive] = useState(AppState.currentState === 'active');
  const [reducedMotion, setReducedMotion] = useState<boolean | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [spinning, setSpinning] = useState(false);
  const [stillShown, setStillShown] = useState(true);
  const [loadBudget] = useState(() => new ShowcaseLoadBudget());
  const loadPhase = useRef(-1);
  const [loadProgress, setLoadProgress] = useState(-1);
  const stillOpacity = useRef(new Animated.Value(1)).current;
  const notify = useRef(onInteractionChange);
  notify.current = onInteractionChange;
  const [touches] = useState(() => new TouchLock(locked => notify.current(locked)));
  const running = active && appActive;
  const live = useRef({ running, spinning, reducedMotion });
  live.current = { running, spinning, reducedMotion };

  const command = useCallback((action: ShowcaseCommand) => {
    viewer.current?.injectJavaScript(showcaseCommandScript(action));
  }, []);
  // The reduced-motion choice is fixed per load, so later changes only stop a running spin.
  const source = useMemo(() => reducedMotion === null ? null : { uri: showcaseUri({ inline: true, reducedMotion }) },
    [reducedMotion]);
  const report = () => viewer.current?.injectJavaScript(`(()=>{const state=window.G60Showcase?.getState();window.ReactNativeWebView?.postMessage(JSON.stringify({type:'diagnostic',phase:'inline',inline:state?.inline,ready:state?.ready,spinning:state?.spinning,spinActive:state?.spinActive,reducedMotion:state?.reducedMotion,quality:state?.quality,renderer:state?.renderer,loadMs:window.G60Showcase?.loadMs,visibility:document.visibilityState}));})();true;`);

  useEffect(() => {
    let mounted = true;
    void AccessibilityInfo.isReduceMotionEnabled().then(value => { if (mounted) setReducedMotion(value); })
      .catch(() => { if (mounted) setReducedMotion(true); });
    const change = AccessibilityInfo.addEventListener('reduceMotionChanged', value => {
      if (!mounted) return;
      setReducedMotion(previous => previous ?? value);
      if (value && live.current.spinning) command('spin');
    });
    return () => { mounted = false; change.remove(); };
  }, [command]);
  useEffect(() => {
    const listener = AppState.addEventListener('change', next => setAppActive(next === 'active'));
    return () => { listener.remove(); accepting.current = false; command('dispose'); touches.release(); };
  }, [command, touches]);
  // Offscreen, covered or backgrounded: stop drawing and give the page back its scroll.
  useEffect(() => {
    command(running ? 'resume' : 'pause');
    if (!running) touches.release();
  }, [running, command, touches]);
  useEffect(() => {
    if (status !== 'loading' || !running || !source) return;
    loadBudget.resume();
    const timeout = setTimeout(() => {
      if (!accepting.current) return;
      accepting.current = false; console.warn(LOG, 'load timed out', loadPhase.current); setStatus('error');
    }, loadBudget.remaining());
    return () => { clearTimeout(timeout); loadBudget.pause(); };
  }, [status, attempt, running, source, loadBudget, loadProgress]);
  useEffect(() => {
    if (status !== 'ready') {
      stillOpacity.setValue(1); setStillShown(true);
      if (status === 'error') touches.release();
      return;
    }
    const fade = Animated.timing(stillOpacity, { toValue: 0, duration: live.current.reducedMotion ? 0 : 600, useNativeDriver: true });
    fade.start(({ finished }) => { if (finished) setStillShown(false); });
    return () => fade.stop();
  }, [status, stillOpacity, touches]);

  const fail = (fromAttempt: number, reason: string) => {
    if (fromAttempt !== currentAttempt.current || !accepting.current) return;
    accepting.current = false; console.warn(LOG, reason); setStatus('error');
  };
  const message = (event: WebViewMessageEvent, fromAttempt: number) => {
    if (fromAttempt !== currentAttempt.current || !accepting.current) return;
    if (event.nativeEvent.data.length <= 1024) console.info(LOG, event.nativeEvent.data);
    const data = parseShowcaseEvent(event.nativeEvent.data);
    if (data?.type === 'progress') {
      const phase = SHOWCASE_LOAD_PHASES.indexOf(data.phase);
      if (phase > loadPhase.current) {
        loadPhase.current = phase; loadBudget.progress(); setLoadProgress(phase);
      }
    } else if (data?.type === 'ready') {
      setStatus('ready');
      command(live.current.running ? 'resume' : 'pause');
      report();
    } else if (data?.type === 'error') fail(fromAttempt, 'page error');
    else if (data?.type === 'state') setSpinning(data.spinning);
  };
  const retry = () => {
    currentAttempt.current += 1; accepting.current = true;
    loadBudget.reset(); loadPhase.current = -1; setLoadProgress(-1);
    setStatus('loading'); setSpinning(false);
    setAttempt(currentAttempt.current);
  };
  const ready = status === 'ready';
  // Touches reach this wrapper only from the viewer; the overlaid buttons are siblings.
  const touchStart = (event: GestureResponderEvent) => {
    if (ready) touches.start(event.nativeEvent.changedTouches, event.nativeEvent.touches.length);
  };
  const touchEnd = (event: GestureResponderEvent) => touches.end(event.nativeEvent.changedTouches, event.nativeEvent.touches.length);

  return <>
    <View accessible accessibilityRole="image"
      accessibilityLabel={`Illustrative G60 model${ready ? spinning ? ', turning' : ', paused' : ''}`}
      onTouchStart={touchStart} onTouchEnd={touchEnd} onTouchCancel={() => touches.release()} style={s.fill}>
      {status !== 'error' && source && <WebView<object> key={attempt} ref={viewer} source={source}
        style={s.webview} containerStyle={s.webContainer}
        accessible={false} importantForAccessibility="no-hide-descendants"
        // Every URL passes the origin list so none is handed to another app; the handler then refuses
        // anything except the bundled document's fixed variants.
        originWhitelist={['*']} onShouldStartLoadWithRequest={request => isShowcaseNavigation(request.url)}
        onMessage={event => message(event, attempt)} onError={() => fail(attempt, 'load error')}
        onRenderProcessGone={() => fail(attempt, 'render process gone')}
        javaScriptEnabled domStorageEnabled={false} allowFileAccess={false}
        allowFileAccessFromFileURLs={false} allowUniversalAccessFromFileURLs={false}
        mixedContentMode="never" setSupportMultipleWindows={false} setBuiltInZoomControls={false}
        nestedScrollEnabled={ready} scrollEnabled={false} bounces={false} overScrollMode="never" />}
    </View>
    {stillShown && <Animated.View pointerEvents="none" accessible={false} importantForAccessibility="no-hide-descendants"
      style={[s.fill, { opacity: stillOpacity }]}>{still}</Animated.View>}
    {ready && <Pressable accessibilityRole="button" accessibilityLabel={spinning ? 'Pause model spin' : 'Spin model'}
      accessibilityState={{ selected: spinning }} hitSlop={4} onPress={() => command('spin')}
      style={({ pressed }) => [s.round, glass, pressed && s.pressed]}>
      {spinning ? <View accessible={false} style={s.pauseGlyph}><View style={s.pauseLine} /><View style={s.pauseLine} /></View>
        : <View accessible={false} style={s.spinGlyph}><View style={s.spinArrow} /></View>}
    </Pressable>}
    {status === 'error' && <Pressable accessibilityRole="button" accessibilityLabel="Retry 3D model" hitSlop={4}
      onPress={retry} style={({ pressed }) => [s.round, glass, pressed && s.pressed]}>
      <View accessible={false} style={s.spinGlyph}><View style={s.spinArrow} /></View>
    </Pressable>}
  </>;
}

const s = StyleSheet.create({
  fill: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center' },
  webContainer: { flex: 1, alignSelf: 'stretch', backgroundColor: 'transparent' },
  webview: { flex: 1, backgroundColor: 'transparent' },
  round: { position: 'absolute', right: '18%', bottom: 8, width: 48, height: 48, borderRadius: 24,
    alignItems: 'center', justifyContent: 'center' },
  pressed: { opacity: 0.7 },
  pauseGlyph: { flexDirection: 'row', gap: 5 }, pauseLine: { width: 3, height: 16, borderRadius: 1, backgroundColor: color.text },
  spinGlyph: { width: 20, height: 20, borderWidth: 2, borderColor: color.text, borderRightColor: 'transparent', borderRadius: 10, transform: [{ rotate: '-30deg' }] },
  spinArrow: { position: 'absolute', right: -1, top: -2, width: 7, height: 7, borderTopWidth: 2, borderRightWidth: 2, borderColor: color.text, transform: [{ rotate: '15deg' }] },
});
