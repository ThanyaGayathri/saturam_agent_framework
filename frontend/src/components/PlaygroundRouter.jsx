import { useDemoProfile } from "../context/DemoProfileContext";
import PlaygroundHomeAlt from "./PlaygroundHomeAlt";
import MiddlebyInsightsHome from "./MiddlebyInsightsHome";

/** Routes Playground home to the correct customer experience. */
export default function PlaygroundRouter() {
  const { isMiddlebyProfile } = useDemoProfile();
  return isMiddlebyProfile ? <MiddlebyInsightsHome /> : <PlaygroundHomeAlt />;
}
