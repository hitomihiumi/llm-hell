import {
  Badge,
  Button,
  Card,
  Column,
  Grid,
  Heading,
  Input,
  Row,
  Select,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
  Text,
  useToast,
} from "@nmmty/dotmatrix";
import { useEffect, useState } from "react";

import { ApiError } from "../api/client";
import {
  endpointsApi,
  type EndpointCheckResult,
  type EndpointCreateInput,
  type ModelEndpoint,
  type Role,
  type ToolsMode,
} from "../api/endpoints";

const EMPTY_FORM: EndpointCreateInput = {
  name: "",
  base_url: "",
  api_key: "",
  model_id: "",
  role: "planner",
  ctx_window: 32768,
  price_per_mtok_in: 0,
  price_per_mtok_out: 0,
  tools_mode: "json_protocol",
};

function StatusBadge({ label, ok }: { label: string; ok: boolean }) {
  return <Badge variant={ok ? "success" : "error"}>{label}: {ok ? "так" : "ні"}</Badge>;
}

function CheckResultView({ result }: { result: EndpointCheckResult }) {
  return (
    <Column gap="12" paddingTop="12" style={{ borderTop: "1px solid var(--dm-border-medium)" }}>
      <Row gap="8" wrap="wrap">
        <StatusBadge label="/v1/models" ok={result.models_ok} />
        <StatusBadge label="/tokenize" ok={result.tokenize_ok} />
        <StatusBadge label="Нативні tool calls" ok={result.native_tools_supported} />
      </Row>
      <Table zebra>
        <TableHead>
          <TableRow>
            <TableHeaderCell>Рівень</TableHeaderCell>
            <TableHeaderCell>Відповів</TableHeaderCell>
            <TableHeaderCell>reasoning_content</TableHeaderCell>
            <TableHeaderCell>Інлайн-теги</TableHeaderCell>
            <TableHeaderCell>Приклад відповіді</TableHeaderCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {result.levels.map((lvl) => (
            <TableRow key={lvl.level}>
              <TableCell>{lvl.level}</TableCell>
              <TableCell>{lvl.ok ? "так" : `помилка: ${lvl.error ?? "?"}`}</TableCell>
              <TableCell>{lvl.reasoning_content_present ? "так" : "ні"}</TableCell>
              <TableCell>{lvl.inline_tags_present ? "так" : "ні"}</TableCell>
              <TableCell>
                <Text fontSize="2xs" color="weak" truncate style={{ maxWidth: 280, display: "block" }}>
                  {lvl.content_sample}
                </Text>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Column>
  );
}

export function AdminEndpointsPage() {
  const toast = useToast();
  const [endpoints, setEndpoints] = useState<ModelEndpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState<EndpointCreateInput>(EMPTY_FORM);
  const [checking, setChecking] = useState<string | null>(null);
  const [checkResults, setCheckResults] = useState<Record<string, EndpointCheckResult>>({});

  async function reload() {
    setLoading(true);
    try {
      const list = await endpointsApi.list();
      setEndpoints(list);
      const results: Record<string, EndpointCheckResult> = {};
      for (const ep of list) {
        if (ep.last_check_result) results[ep.id] = ep.last_check_result;
      }
      setCheckResults(results);
    } catch (err) {
      toast.show({
        title: "Не вдалося завантажити ендпоінти",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "error",
      });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    try {
      await endpointsApi.create(form);
      setForm(EMPTY_FORM);
      await reload();
    } catch (err) {
      toast.show({
        title: "Не вдалося створити ендпоінт",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "error",
      });
    }
  }

  async function onToggleEnabled(ep: ModelEndpoint) {
    await endpointsApi.update(ep.id, { enabled: !ep.enabled });
    await reload();
  }

  async function onDelete(id: string) {
    await endpointsApi.remove(id);
    await reload();
  }

  async function onCheck(id: string) {
    setChecking(id);
    try {
      const result = await endpointsApi.check(id);
      setCheckResults((prev) => ({ ...prev, [id]: result }));
    } catch (err) {
      toast.show({
        title: "Перевірка не вдалася",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "error",
      });
    } finally {
      setChecking(null);
    }
  }

  return (
    <Column as="main" padding="32" gap="24" style={{ maxWidth: 960, margin: "0 auto" }}>
      <Heading as="h1">Ендпоінти моделей</Heading>

      <Card as="form" onSubmit={onCreate} padding="20" gap="16">
        <Heading as="h2" fontSize="s">
          Новий ендпоінт
        </Heading>
        <Grid columns="2" gap="16">
          <Input label="Назва" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          <Select
            label="Роль"
            value={form.role}
            onChange={(v) => setForm({ ...form, role: v as Role })}
            options={[
              { value: "planner", label: "Проєктувальник (planner)" },
              { value: "executor", label: "Виконавець (executor)" },
            ]}
          />
          <Input
            label="Base URL (напр. http://host:8000/v1)"
            value={form.base_url}
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            required
          />
          <Input
            label="Model ID"
            value={form.model_id}
            onChange={(e) => setForm({ ...form, model_id: e.target.value })}
            required
          />
          <Input
            label="API-ключ (необов'язково)"
            value={form.api_key}
            onChange={(e) => setForm({ ...form, api_key: e.target.value })}
          />
          <Input
            label="Вікно контексту (токенів)"
            type="number"
            value={form.ctx_window}
            onChange={(e) => setForm({ ...form, ctx_window: Number(e.target.value) })}
          />
          <Select
            label="Режим tool calls"
            value={form.tools_mode}
            onChange={(v) => setForm({ ...form, tools_mode: v as ToolsMode })}
            options={[
              { value: "json_protocol", label: "JSON-протокол (сумісно з усіма)" },
              { value: "native", label: "Нативний OpenAI tools" },
            ]}
          />
        </Grid>
        <Row>
          <Button type="submit">Додати ендпоінт</Button>
        </Row>
      </Card>

      {loading ? (
        <Text color="weak">Завантаження...</Text>
      ) : (
        <Column gap="16">
          {endpoints.map((ep) => (
            <Card key={ep.id} padding="20" gap="12">
              <Row justifyContent="between" alignItems="start" gap="16">
                <Column gap="2">
                  <Text weight="bold">
                    {ep.name} ({ep.role === "planner" ? "проєктувальник" : "виконавець"}) — {ep.model_id}
                  </Text>
                  <Text fontSize="s" color="weak">
                    {ep.base_url} · вікно {ep.ctx_window.toLocaleString("uk-UA")} токенів · tools:{" "}
                    {ep.tools_mode === "native" ? "нативний" : "JSON-протокол"} ·{" "}
                    {ep.enabled ? "увімкнено" : "вимкнено"}
                    {ep.last_checked_at && ` · перевірено ${new Date(ep.last_checked_at).toLocaleString("uk-UA")}`}
                  </Text>
                </Column>
                <Row gap="8" style={{ flexShrink: 0 }}>
                  <Button size="s" variant="outline" onClick={() => onCheck(ep.id)} disabled={checking === ep.id}>
                    {checking === ep.id ? "Перевіряємо..." : "Перевірити ендпоінт"}
                  </Button>
                  <Button size="s" variant="outline" onClick={() => onToggleEnabled(ep)}>
                    {ep.enabled ? "Вимкнути" : "Увімкнути"}
                  </Button>
                  <Button size="s" variant="ghost" onClick={() => onDelete(ep.id)}>
                    Видалити
                  </Button>
                </Row>
              </Row>
              {checkResults[ep.id] && <CheckResultView result={checkResults[ep.id]} />}
            </Card>
          ))}
        </Column>
      )}
    </Column>
  );
}
