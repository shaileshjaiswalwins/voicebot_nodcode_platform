import type { PhraseCategory } from '../api';

export const PHRASE_CATEGORY_META: { id: PhraseCategory; label: string; description: string }[] = [
  {
    id: 'voicemail',
    label: 'Voicemail phrases',
    description: 'When a call hits an automated answering machine. Adding more lines here makes the bot give up faster on dead numbers.'
  },
  {
    id: 'hold_music',
    label: 'Hold-music phrases',
    description: 'PBX/carrier messages played when a person picks up but parks the call. Adding regional language phrases helps detect these.'
  },
  {
    id: 'dnc_trigger',
    label: 'Do-not-call triggers',
    description: 'Phrases that mean the caller wants to be removed from outreach. Detection is used by the bot to end the call respectfully.'
  }
];
