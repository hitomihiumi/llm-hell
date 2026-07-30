import { Button, Card, Column, Heading, Input, PasswordInput, Row, Text, useToast } from "@nmmty/dotmatrix";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { authApi } from "../api/auth";
import { ApiError } from "../api/client";
import { useAuthStore } from "../store/auth";

export function LoginPage() {
  const navigate = useNavigate();
  const setUser = useAuthStore((s) => s.setUser);
  const toast = useToast();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      const user = await authApi.login(username, password);
      setUser(user);
      navigate("/");
    } catch (err) {
      toast.show({
        title: "Не вдалося увійти",
        description: err instanceof ApiError ? err.message : undefined,
        variant: "error",
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Column as="main" height="screen" alignItems="center" justifyContent="center">
      <Card as="form" onSubmit={onSubmit} padding="32" gap="16" style={{ width: 360 }}>
        <Heading as="h1" fontSize="l">
          Вхід до LLM-Hell
        </Heading>
        <Input
          label="Ім'я користувача"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
          autoFocus
        />
        <PasswordInput
          label="Пароль"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <Button type="submit" disabled={submitting} width="full">
          {submitting ? "Входимо..." : "Увійти"}
        </Button>
        <Row justifyContent="center" gap="4">
          <Text color="weak" fontSize="s">
            Немає акаунта?
          </Text>
          <Button as={Link} to="/register" variant="ghost" size="s">
            Зареєструватися за інвайт-кодом
          </Button>
        </Row>
      </Card>
    </Column>
  );
}
