use crate::openai::ChatMessage;

/// How OpenAI-style messages are flattened into a single CLI prompt.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FlattenMode {
    Flat,
    FoldSystem,
    SystemLast,
}

impl FlattenMode {
    pub fn parse(raw: &str) -> Self {
        match raw.trim().to_lowercase().as_str() {
            "fold_system" | "fold-system" | "fold_system_into_user" => Self::FoldSystem,
            "system_last" | "system-last" => Self::SystemLast,
            _ => Self::Flat,
        }
    }
}

pub fn build_prompt(
    messages: &[ChatMessage],
    flatten: FlattenMode,
    prefix: &str,
    suffix: &str,
    json_mode: bool,
) -> String {
    let body = flatten_messages(messages, flatten);
    let mut parts = Vec::new();
    if !prefix.trim().is_empty() {
        parts.push(prefix.trim().to_string());
    }
    if !body.is_empty() {
        parts.push(body);
    }
    if json_mode {
        parts.push(
            "Return ONLY valid JSON (object or array). No markdown fences, no commentary before or after."
                .into(),
        );
    }
    if !suffix.trim().is_empty() {
        parts.push(suffix.trim().to_string());
    }
    parts.join("\n\n")
}

pub fn flatten_messages(messages: &[ChatMessage], mode: FlattenMode) -> String {
    match mode {
        FlattenMode::Flat => flatten_flat(messages),
        FlattenMode::FoldSystem => flatten_fold_system(messages),
        FlattenMode::SystemLast => flatten_system_last(messages),
    }
}

fn flatten_flat(messages: &[ChatMessage]) -> String {
    let mut parts = Vec::new();
    for msg in messages {
        let role = msg.role.as_str();
        let content = msg.content_as_text();
        if content.is_empty() {
            continue;
        }
        let block = match role {
            "system" => format!("System:\n{content}"),
            "user" => format!("User:\n{content}"),
            "assistant" => format!("Assistant:\n{content}"),
            "tool" => format!("Tool:\n{content}"),
            other => format!("{other}:\n{content}"),
        };
        parts.push(block);
    }
    parts.join("\n\n")
}

fn flatten_fold_system(messages: &[ChatMessage]) -> String {
    let mut system_chunks = Vec::new();
    let mut other = Vec::new();
    for msg in messages {
        let content = msg.content_as_text();
        if content.is_empty() {
            continue;
        }
        if msg.role.eq_ignore_ascii_case("system") {
            system_chunks.push(content);
        } else {
            other.push(msg);
        }
    }
    let mut parts = Vec::new();
    if !system_chunks.is_empty() && !other.is_empty() {
        let first = &other[0];
        if first.role.eq_ignore_ascii_case("user") {
            let merged = format!(
                "System instructions:\n{}\n\nUser:\n{}",
                system_chunks.join("\n\n"),
                first.content_as_text()
            );
            parts.push(merged);
            for msg in other.iter().skip(1) {
                parts.push(role_block(msg));
            }
            return parts.join("\n\n");
        }
    }
    for chunk in system_chunks {
        parts.push(format!("System:\n{chunk}"));
    }
    for msg in other {
        parts.push(role_block(msg));
    }
    parts.join("\n\n")
}

fn flatten_system_last(messages: &[ChatMessage]) -> String {
    let mut system_chunks = Vec::new();
    let mut dialogue = Vec::new();
    for msg in messages {
        let content = msg.content_as_text();
        if content.is_empty() {
            continue;
        }
        if msg.role.eq_ignore_ascii_case("system") {
            system_chunks.push(content);
        } else {
            dialogue.push(role_block(msg));
        }
    }
    let mut parts = dialogue;
    if !system_chunks.is_empty() {
        parts.push(format!(
            "Constraints (must follow):\n{}",
            system_chunks.join("\n\n")
        ));
    }
    parts.join("\n\n")
}

fn role_block(msg: &ChatMessage) -> String {
    let content = msg.content_as_text();
    match msg.role.as_str() {
        "user" => format!("User:\n{content}"),
        "assistant" => format!("Assistant:\n{content}"),
        "tool" => format!("Tool:\n{content}"),
        other => format!("{other}:\n{content}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::openai::{ChatMessage, MessageContent};

    fn msg(role: &str, text: &str) -> ChatMessage {
        ChatMessage {
            role: role.into(),
            content: MessageContent::Text(text.into()),
        }
    }

    #[test]
    fn fold_system_merges_into_first_user() {
        let messages = vec![
            msg("system", "Be concise."),
            msg("user", "Hello"),
        ];
        let out = flatten_messages(&messages, FlattenMode::FoldSystem);
        assert!(out.contains("System instructions:"));
        assert!(out.contains("User:\nHello"));
    }
}
