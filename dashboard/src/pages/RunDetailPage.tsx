import { useParams } from "react-router-dom";

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();

  return (
    <div>
      <h1 className="mb-6 text-2xl font-semibold tracking-tight text-foreground">Run Detail</h1>
      <p className="text-muted">Run ID: {id}</p>
    </div>
  );
}
