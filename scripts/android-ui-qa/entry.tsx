import React from 'react';
import { createRoot } from 'react-dom/client';
import { Platform } from 'react-native';
import App from '../../apps/android/App';

// Match Android permission copy while retaining the web renderer. ?api=30
// inspects the legacy location-permission guidance without a native runtime.
Object.defineProperty(Platform, 'Version', { get: () => Number(new URLSearchParams(location.search).get('api') ?? 35) });
document.body.style.margin = '0';
document.body.style.background = '#150505';
document.body.style.overflow = 'hidden';
document.getElementById('root')!.style.height = '100dvh';
document.getElementById('root')!.style.display = 'flex';
document.getElementById('root')!.style.flexDirection = 'column';
createRoot(document.getElementById('root')!).render(<App />);
