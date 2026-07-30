import {
  Button,
  Column,
  Heading,
  IconButton,
  InlineCode,
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

import { invitesApi, type Invite } from "../api/invites";
import { ApiError } from "../api/client";

export function AdminInvitesPage() {
  const toast = useToast();
  const [invites, setInvites] = useState<Invite[]>([]);
  const [role, setRole] = useState<"admin" | "user">("user");
  const [loading, setLoading] = useState(true);

  async function reload() {
    setLoading(true);
    try {
      setInvites(await invitesApi.list());
    } catch (err) {
      toast.show({
        title: "Не вдалося завантажити інвайти",
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

  async function onCreate() {
    try {
      await invitesApi.create(role);
      await reload();
    } catch (err) {
      toast.show({
        title: "Не вдалося створити інвайт",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "error",
      });
    }
  }

  async function onDelete(id: string) {
    await invitesApi.remove(id);
    await reload();
  }

  return (
    <Column as="main" padding="32" gap="20" style={{ maxWidth: 720, margin: "0 auto" }}>
      <Heading as="h1">Інвайт-коди</Heading>

      <Row gap="8" alignItems="end">
        <Select
          label="Роль"
          value={role}
          onChange={(v) => setRole(v as "admin" | "user")}
          options={[
            { value: "user", label: "Учасник тестування" },
            { value: "admin", label: "Адміністратор" },
          ]}
        />
        <Button onClick={onCreate}>Створити інвайт</Button>
      </Row>

      {loading ? (
        <Text color="weak">Завантаження...</Text>
      ) : (
        <Table zebra>
          <TableHead>
            <TableRow>
              <TableHeaderCell>Код</TableHeaderCell>
              <TableHeaderCell>Роль</TableHeaderCell>
              <TableHeaderCell>Статус</TableHeaderCell>
              <TableHeaderCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {invites.map((inv) => (
              <TableRow key={inv.id}>
                <TableCell>
                  <InlineCode>{inv.code}</InlineCode>
                </TableCell>
                <TableCell>{inv.role === "admin" ? "Адміністратор" : "Учасник тестування"}</TableCell>
                <TableCell>{inv.used_by ? "Використано" : "Активний"}</TableCell>
                <TableCell>
                  <IconButton
                    icon="trash"
                    aria-label={`Видалити інвайт ${inv.code}`}
                    variant="ghost"
                    size="s"
                    onClick={() => onDelete(inv.id)}
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Column>
  );
}
