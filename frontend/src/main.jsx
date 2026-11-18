import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import Presenter from "./Presenter";
import OpsBoard from "./OpsBoard";
import RobotScreen from "./RobotScreen";
import "./styles.css";
import "./ops.css";

function useHash() {
  const [h, setH] = useState(window.location.hash);
  useEffect(() => {
    const f = () => setH(window.location.hash);
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  return h;
}

function App() {
  const hash = useHash();
  const m = hash.match(/^#\/robot\/([\w-]+)/);
  if (m) return <RobotScreen robotId={m[1]} />;
  if (hash.startsWith("#/presenter")) return <Presenter />;
  return <OpsBoard />;
}

createRoot(document.getElementById("root")).render(<App />);
