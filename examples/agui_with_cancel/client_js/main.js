import { HttpAgent } from '@ag-ui/client';

const agent = new HttpAgent({
  url: 'http://127.0.0.1:18080/weather_agent',
  debug: false
});

let chunkCount = 0;
const ABORT_AFTER_CHUNKS = 5;

const subscription = agent.subscribe({
  onTextMessageStartEvent: ({ event }) => {
    process.stdout.write('\n🤖 Assistant: ');
  },
  onTextMessageContentEvent: ({ event }) => {
    process.stdout.write(event.delta ?? '');
    chunkCount++;
    if (chunkCount === ABORT_AFTER_CHUNKS) {
      process.stdout.write('\n\n⏸️  Aborting run after receiving ' + ABORT_AFTER_CHUNKS + ' text chunks...\n');
      agent.abortRun();
    }
  },
  onTextMessageEndEvent: ({ event }) => {
    process.stdout.write('\n');
  },
  onToolCallStartEvent: ({ event }) => {
    process.stdout.write(`\n🔧 Call Tool ${event.toolCallName}: `);
  },
  onToolCallArgsEvent: ({ event }) => {
    process.stdout.write(event.delta ?? '');
  },
  onToolCallResultEvent: ({ event }) => {
    process.stdout.write(`\n✅ Tool result: ${event.content}`);
  },
  onRunStartedEvent: ({ event }) => {
    process.stdout.write(`\n⚙️  Run started: ${event.runId}`);
  },
  onRunFinishedEvent: ({ result }) => {
    if (result !== undefined) {
      process.stdout.write(`⚙️  Run finished, result: ${result}\n`);
    } else {
      process.stdout.write('⚙️  Run finished\n');
    }
  },
  onRunFailedEvent: ({ error }) => {
    process.stdout.write(`❌ Run failed: ${error}\n`);
  }
});

await agent.addMessage({
  role: 'user',
  content: 'Please introduce yourself in detail and tell me what you can do.',
  id: 'user_123'
});

await agent.runAgent();

subscription.unsubscribe?.();
