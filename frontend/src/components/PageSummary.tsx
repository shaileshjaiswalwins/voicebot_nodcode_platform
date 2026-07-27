import React from 'react';
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  BarChart2,
  BookOpen,
  Bot,
  Braces,
  CheckCircle2,
  Clock3,
  ClipboardList,
  Database,
  FileText,
  Megaphone,
  MessageSquareText,
  Mic,
  PhoneCall,
  ShieldCheck,
  Volume2,
  Wand2,
  Wifi
} from 'lucide-react';
import type { View } from '../types';
import type { Bot as BotType, Campaign, Transcript } from '../api';
import { average } from '../utils/errors';
import { SummaryCard } from './SummaryCard';

export function PageSummary({
  view, bots, transcripts, campaigns, selectedBot,
  testStatus, roomName, micEnabled, remoteAudioReady, loading
}: {
  view: View;
  bots: BotType[];
  transcripts: Transcript[];
  campaigns: Campaign[];
  selectedBot?: BotType;
  testStatus: string;
  roomName: string;
  micEnabled: boolean;
  remoteAudioReady: boolean;
  loading: boolean;
}) {
  const L = loading ? '…' : null;

  if (view === 'bots' || view === 'builder') {
    const total = bots.length;
    const active = bots.filter((b) => b.status === 'active').length;
    const draft = bots.filter((b) => b.status === 'paused').length;
    return (
      <div className="page-summary">
        <SummaryCard icon={<Bot size={17} />} color="blue" label="Total agents" value={L ?? total.toString()} sub="All configured bots" />
        <SummaryCard icon={<CheckCircle2 size={17} />} color="green" label="Active" value={L ?? active.toString()} sub="Published & running" />
        <SummaryCard icon={<Braces size={17} />} color="amber" label="Draft" value={L ?? draft.toString()} sub="Awaiting publish" />
      </div>
    );
  }

  if (view === 'campaigns') {
    const total = campaigns.length;
    const withBot = campaigns.filter((c) => c.bot_id).length;
    const active = campaigns.filter((c) => c.status === 'active').length;
    const noBot = campaigns.filter((c) => !c.bot_id).length;
    return (
      <div className="page-summary">
        <SummaryCard icon={<Megaphone size={17} />} color="blue" label="Total campaigns" value={L ?? total.toString()} sub="All campaign keys" />
        <SummaryCard icon={<CheckCircle2 size={17} />} color="green" label="Active" value={L ?? active.toString()} sub="Status: active" />
        <SummaryCard icon={<Bot size={17} />} color="amber" label="Bot assigned" value={L ?? withBot.toString()} sub="Have a bot mapping" />
        <SummaryCard icon={<AlertCircle size={17} />} color="red" label="No bot" value={L ?? noBot.toString()} sub="Need assignment" />
      </div>
    );
  }

  if (view === 'test') {
    const connected = Boolean(roomName) && !testStatus.toLowerCase().includes('failed') && testStatus !== 'Idle';
    return (
      <div className="page-summary">
        <SummaryCard icon={<Bot size={17} />} color="blue" label="Selected bot" value={selectedBot?.name || '—'} sub="Active config" />
        <SummaryCard icon={<Wifi size={17} />} color={connected ? 'green' : 'slate'} label="Session" value={connected ? 'Live' : 'Idle'} sub={testStatus} />
        <SummaryCard icon={<Mic size={17} />} color={micEnabled ? 'green' : 'slate'} label="Microphone" value={micEnabled ? 'Live' : 'Off'} sub="Input status" />
        <SummaryCard icon={<Volume2 size={17} />} color={remoteAudioReady ? 'green' : 'slate'} label="Bot audio" value={remoteAudioReady ? 'Connected' : 'Waiting'} sub="Output status" />
      </div>
    );
  }

  if (view === 'transcripts') {
    const total = transcripts.length;
    const completed = transcripts.filter((t) => t.status === 'completed').length;
    const avgDur = average(transcripts.map((t) => Number(t.call_duration_sec || 0)).filter(Boolean));
    const now = new Date();
    const today = transcripts.filter((t) => {
      if (!t.created_at) return false;
      const d = new Date(t.created_at);
      return d.getDate() === now.getDate() && d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear();
    }).length;
    return (
      <div className="page-summary">
        <SummaryCard icon={<FileText size={17} />} color="blue" label="Total calls" value={L ?? total.toString()} sub="All transcripts" />
        <SummaryCard icon={<CheckCircle2 size={17} />} color="green" label="Completed" value={L ?? completed.toString()} sub="Status: completed" />
        <SummaryCard icon={<Clock3 size={17} />} color="amber" label="Avg duration" value={avgDur ? `${avgDur}s` : '—'} sub="From saved records" />
        <SummaryCard icon={<Activity size={17} />} color="slate" label="Today" value={L ?? today.toString()} sub="Calls today" />
      </div>
    );
  }

  if (view === 'observability') {
    const total = transcripts.length;
    const nonCompleted = transcripts.filter((t) => t.status && t.status !== 'completed').length;
    const completed = transcripts.filter((t) => t.status === 'completed').length;
    return (
      <div className="page-summary">
        <SummaryCard icon={<Activity size={17} />} color="blue" label="Total traces" value={L ?? total.toString()} sub="Transcripts stored" />
        <SummaryCard icon={<CheckCircle2 size={17} />} color="green" label="Completed" value={L ?? completed.toString()} sub="Clean calls" />
        <SummaryCard icon={<AlertTriangle size={17} />} color="amber" label="Non-completed" value={L ?? nonCompleted.toString()} sub="Errors / incomplete" />
        <SummaryCard icon={<Bot size={17} />} color="slate" label="Agent" value={selectedBot?.name || '—'} sub="Selected bot" />
      </div>
    );
  }

  if (view === 'library') {
    return (
      <div className="page-summary">
        <SummaryCard icon={<BookOpen size={17} />} color="blue" label="Library" value="Phrase DB" sub="Voicemail · Hold · DNC" />
        <SummaryCard icon={<MessageSquareText size={17} />} color="green" label="Outcomes" value="AI labels" sub="Call classification" />
        <SummaryCard icon={<Wand2 size={17} />} color="amber" label="Languages" value="Style notes" sub="Per-language config" />
        <SummaryCard icon={<ShieldCheck size={17} />} color="slate" label="Live in" value="~60s" sub="Edits go live fast" />
      </div>
    );
  }

  if (view === 'settings') {
    return (
      <div className="page-summary">
        <SummaryCard icon={<ClipboardList size={17} />} color="blue" label="Runtime" value="LiveKit" sub="Connection settings" />
        <SummaryCard icon={<Wifi size={17} />} color="green" label="Agent name" value="Worker ID" sub="LiveKit dispatch key" />
        <SummaryCard icon={<ShieldCheck size={17} />} color="amber" label="Credentials" value="Backend .env" sub="Keys not shown here" />
        <SummaryCard icon={<Database size={17} />} color="slate" label="Mongo" value="Connected" sub="Auto-managed" />
      </div>
    );
  }

  // Fallback — global overview
  const activeBots = bots.filter((b) => b.status === 'active').length;
  const completedCalls = transcripts.filter((t) => t.status === 'completed').length;
  const avgDuration = average(transcripts.map((t) => Number(t.call_duration_sec || 0)).filter(Boolean));
  return (
    <div className="page-summary">
      <SummaryCard icon={<Bot size={17} />} color="blue" label="Active agents" value={L ?? activeBots.toString()} sub={`${bots.length} total`} />
      <SummaryCard icon={<PhoneCall size={17} />} color="amber" label="Completed calls" value={L ?? completedCalls.toString()} sub={`${transcripts.length} transcripts`} />
      <SummaryCard icon={<Clock3 size={17} />} color="slate" label="Avg duration" value={avgDuration ? `${avgDuration}s` : '—'} sub="Saved transcripts" />
    </div>
  );
}
