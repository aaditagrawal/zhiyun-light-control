import React, { forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from 'react';
import { View } from 'react-native';
import { SHOWCASE_HTML } from '../../apps/android/src/showcase/document';

// Fixture-only adapter: the same embedded viewer runs inside a sandboxed iframe.
// The installed Android app uses the real native WebView module.
export const WebView = forwardRef(function WebView({ source, onMessage, style, containerStyle }: any, ref) {
  if (new URLSearchParams(location.search).get('viewerFail') === 'module') throw new Error('Fixture WebView module unavailable');
  const frame = useRef<HTMLIFrameElement>(null);
  const html = useMemo(() => (source.html ?? SHOWCASE_HTML).replace(/<head[^>]*>/i,
    `$&<script>window.__LIGHTTIME_REDUCED_MOTION__=${source.uri?.includes('reduce-motion') === true};window.__LIGHTTIME_INLINE__=${source.uri?.includes('#inline') === true};window.ReactNativeWebView={postMessage:function(data){parent.postMessage({type:"lighttime-showcase-event",data:data},"*")}};<\/script>`), [source]);
  useImperativeHandle(ref, () => ({
    injectJavaScript(script: string) {
      const match = script.match(/command\(("(?:\\.|[^"\\])*")\)/);
      if (match) frame.current?.contentWindow?.postMessage({ type: 'lighttime-showcase-command', action: JSON.parse(match[1]!) }, '*');
    },
  }), []);
  useEffect(() => {
    const listener = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.type !== 'lighttime-showcase-event') return;
      onMessage?.({ nativeEvent: { data: event.data.data } });
    };
    window.addEventListener('message', listener);
    return () => window.removeEventListener('message', listener);
  }, [onMessage]);
  return <View style={[{ flex: 1 }, containerStyle, style]}>
    <iframe ref={frame} title="Interactive G60 model" sandbox="allow-scripts" srcDoc={html}
      style={{ width: '100%', height: '100%', flex: 1, border: 0, background: 'transparent' }} />
  </View>;
});
