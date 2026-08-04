import React from 'react';
import Joyride, { CallBackProps, STATUS, Step } from 'react-joyride';
import { cacheGet, cacheSet } from '../utils/cache';

const STEPS: Step[] = [
  {
    target: 'body',
    placement: 'center',
    title: 'Welcome to the Voice AI Platform',
    content: "Let's take 30 seconds to show you around before you build your first agent.",
    disableBeacon: true,
  },
  {
    target: '#onboarding-nav-agents',
    title: 'Agents',
    content: 'Every voice bot you build lives here. This is home base.',
  },
  {
    target: '#onboarding-create-agent',
    title: 'Create your first agent',
    content: "Click here any time. You can describe it in plain English, start from a template, or build it field by field — whichever's fastest for you.",
  },
  {
    target: '#onboarding-nav-dashboard',
    title: 'Dashboard',
    content: "Once you're live, this page tracks call volume and outcomes across all your agents, refreshing automatically.",
  },
];

/** First-login product tour — gated on a one-time localStorage flag, same pattern as the
 * confetti burst in App.tsx. There's no server-side "just signed up" signal (see
 * api.login response shape), so `hasAgents === false` on first load is the best available
 * proxy for "brand new account" and keeps this frontend-only, no backend change needed. */
export function OnboardingTour({ run }: { run: boolean }) {
  if (cacheGet('hasSeenOnboardingTour', false)) return null;

  const handleCallback = (data: CallBackProps) => {
    if (data.status === STATUS.FINISHED || data.status === STATUS.SKIPPED) {
      cacheSet('hasSeenOnboardingTour', true);
    }
  };

  return (
    <Joyride
      steps={STEPS}
      run={run}
      continuous
      showSkipButton
      showProgress
      scrollToFirstStep
      disableScrolling
      callback={handleCallback}
      locale={{ last: 'Done', skip: 'Skip tour' }}
      styles={{
        options: {
          primaryColor: '#6366f1',
          zIndex: 10000,
          arrowColor: '#ffffff',
          backgroundColor: '#ffffff',
          textColor: '#0f172a',
        },
      }}
    />
  );
}
