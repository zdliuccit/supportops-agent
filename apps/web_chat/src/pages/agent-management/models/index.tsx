import { useModelManagement } from "./hooks/useModelManagement";
import { ModelList } from "./components/ModelList";
import { ModelManagementHeader } from "./components/ModelManagementHeader";
import { ModelManagementDialogs } from "./components/ModelManagementDialogs";

export function ModelManagementPage() {
 const state = useModelManagement();
 return <>
  <ModelManagementHeader state={state} />
  <ModelList state={state} />
  <ModelManagementDialogs state={state} />
 </>;
}
