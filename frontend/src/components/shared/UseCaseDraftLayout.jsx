import { Outlet } from "react-router-dom";
import { UseCaseDraftProvider } from "../../context/UseCaseDraftContext";

export default function UseCaseDraftLayout() {
  return (
    <UseCaseDraftProvider>
      <Outlet />
    </UseCaseDraftProvider>
  );
}
