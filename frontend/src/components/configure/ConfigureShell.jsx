import { Outlet } from "react-router-dom";
import { ConfigureWorkspaceProvider } from "../../context/ConfigureWorkspaceContext";
import ConfigureLayout from "../shared/ConfigureLayout";

export default function ConfigureShell() {
  return (
    <ConfigureWorkspaceProvider>
      <ConfigureLayout>
        <Outlet />
      </ConfigureLayout>
    </ConfigureWorkspaceProvider>
  );
}
