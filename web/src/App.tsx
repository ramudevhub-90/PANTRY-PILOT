import { HAS_SERVER } from './agent/remote';
import { AuthGate } from './auth/AuthGate';
import { AssistantPanel } from './components/AssistantPanel';
import { ConnectorsCard } from './components/ConnectorsCard';
import { GroceryCard } from './components/GroceryCard';
import { Header } from './components/Header';
import { InventoryCard } from './components/InventoryCard';
import { MealPlanCard } from './components/MealPlanCard';
import { ScanReview } from './components/ScanReview';
import { StatsStrip } from './components/StatsStrip';
import { TimersCard } from './components/TimersCard';
import { TimerWatcher } from './components/TimerWatcher';
import { PantryProvider } from './state/store';

export default function App() {
  return (
    <AuthGate>
      <PantryApp />
    </AuthGate>
  );
}

function PantryApp() {
  return (
    <PantryProvider>
      <div className="app">
        <Header />
        <main className="layout">
          <AssistantPanel />
          <section className="dashboard" aria-label="Kitchen dashboard">
            <StatsStrip />
            <div className="board">
              <MealPlanCard />
              <InventoryCard />
              <GroceryCard />
              <TimersCard />
              {HAS_SERVER && <ConnectorsCard />}
            </div>
          </section>
        </main>
      </div>
      <ScanReview />
      <TimerWatcher />
    </PantryProvider>
  );
}
