use serde_json::Value;

/// Strip markdown code fences and leading prose before JSON payloads.
pub fn normalize_output(text: &str, json_mode: bool) -> String {
    let trimmed = text.trim();
    if !json_mode {
        return trimmed.to_string();
    }
    if let Some(json) = extract_json_payload(trimmed) {
        return json;
    }
    trimmed.to_string()
}

pub fn extract_json_payload(text: &str) -> Option<String> {
    let stripped = strip_markdown_fences(text.trim());
    if let Ok(value) = serde_json::from_str::<Value>(&stripped) {
        return Some(value.to_string());
    }
    if let Some(slice) = find_json_slice(&stripped) {
        if serde_json::from_str::<Value>(slice).is_ok() {
            return Some(slice.to_string());
        }
    }
    None
}

pub fn looks_like_json(text: &str) -> bool {
    let t = text.trim();
    (t.starts_with('{') && t.ends_with('}')) || (t.starts_with('[') && t.ends_with(']'))
}

fn strip_markdown_fences(text: &str) -> String {
    let s = text.to_string();
    if s.starts_with("```") {
        if let Some(end) = s.rfind("```") {
            let inner = s[s.find('\n').unwrap_or(3)..end].trim();
            return inner.to_string();
        }
    }
    s
}

fn find_json_slice(text: &str) -> Option<&str> {
    let start_obj = text.find('{');
    let start_arr = text.find('[');
    let start = match (start_obj, start_arr) {
        (Some(a), Some(b)) => Some(a.min(b)),
        (Some(a), None) => Some(a),
        (None, Some(b)) => Some(b),
        _ => None,
    }?;
    let tail = &text[start..];
    if tail.starts_with('{') {
        return matching_json_end(tail, '{', '}');
    }
    matching_json_end(tail, '[', ']')
}

fn matching_json_end(text: &str, open: char, close: char) -> Option<&str> {
    let mut depth = 0i32;
    let mut in_string = false;
    let mut escape = false;
    for (idx, ch) in text.char_indices() {
        if in_string {
            if escape {
                escape = false;
            } else if ch == '\\' {
                escape = true;
            } else if ch == '"' {
                in_string = false;
            }
            continue;
        }
        match ch {
            '"' => in_string = true,
            c if c == open => depth += 1,
            c if c == close => {
                depth -= 1;
                if depth == 0 {
                    return Some(&text[..=idx]);
                }
            }
            _ => {}
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_fenced_json() {
        let raw = "Here is the result:\n```json\n{\"a\":1}\n```";
        assert_eq!(normalize_output(raw, true), "{\"a\":1}");
    }

    #[test]
    fn extracts_array_from_prose() {
        let raw = "Analysis complete.\n[{\"id\":\"x\"}]";
        assert_eq!(normalize_output(raw, true), "[{\"id\":\"x\"}]");
    }
}
