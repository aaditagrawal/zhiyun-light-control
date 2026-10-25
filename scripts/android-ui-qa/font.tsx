import { useEffect, useState } from 'react';

// The fixture loads the app's real bundled font files through browser FontFace.
// Android continues to use expo-font; this module is selected only by build.ts.
export function useFonts(sources: Record<string, string>): [boolean, Error | null] {
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  useEffect(() => {
    let active = true;
    void Promise.all(Object.entries(sources).map(async ([family, source]) => {
      const face = await new FontFace(family, `url("${source}")`,
        family === 'PlayfairDisplay' ? { weight: '400 900' } : {}).load();
      document.fonts.add(face);
    })).then(() => { if (active) setLoaded(true); }, reason => {
      if (active) setError(reason instanceof Error ? reason : new Error(String(reason)));
    });
    return () => { active = false; };
  }, []);
  return [loaded, error];
}
