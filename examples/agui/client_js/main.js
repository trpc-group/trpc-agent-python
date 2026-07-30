import { HttpAgent } from '@ag-ui/client';

const agent = new HttpAgent({
  url: 'http://127.0.0.1:18080/weather_agent',
  debug: false
});

const subscription = agent.subscribe({
  // Text message start
  onTextMessageStartEvent: ({ event }) => {
    process.stdout.write('\n🤖 Assistant: ');
  },
  // Text message content delta
  onTextMessageContentEvent: ({ event }) => {
    process.stdout.write(event.delta ?? '');
  },
  // Text message end
  onTextMessageEndEvent: ({ event }) => {
    process.stdout.write('\n');
  },
  // Tool call start
  onToolCallStartEvent: ({ event }) => {
    process.stdout.write(`\n🔧 Call Tool ${event.toolCallName}: `);
  },
  // Tool call arguments delta
  onToolCallArgsEvent: ({ event }) => {
    process.stdout.write(event.delta ?? '');
  },
  // Tool call result
  onToolCallResultEvent: ({ event }) => {
    process.stdout.write(`\n✅ Tool result: ${event.content}`);
  },
  // Run started
  onRunStartedEvent: ({ event }) => {
    process.stdout.write(`\n⚙️  Run started: ${event.runId}`);
  },
  // Run finished
  onRunFinishedEvent: ({ result }) => {
    if (result !== undefined) {
      process.stdout.write(`⚙️  Run finished, result: ${result}\n`);
    } else {
      process.stdout.write('⚙️  Run finished\n');
    }
  },
  // Run failed
  onRunFailedEvent: ({ error }) => {
    process.stdout.write(`❌ Run failed: ${error}\n`);
  }
});

// Add user message
await agent.addMessage({
  role: 'user',
  content: 'What is the weather like in Beijing?',
  id: 'user_123'
});

// Execute conversation (automatically sends message)
await agent.runAgent();

// Unsubscribe when done
subscription.unsubscribe?.();
