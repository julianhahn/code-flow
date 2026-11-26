//! Evidence validation and deterministic layout for the documented flow format.
use anyhow::{anyhow, bail, ensure, Context, Result};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::{
    fs,
    path::{Path, PathBuf},
};

#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct Source {
    pub file: String,
    pub line: usize,
    pub sha256: String,
}
#[derive(Debug, Clone, Deserialize)]
pub struct Region {
    pub id: String,
    pub label: String,
    pub parent: Option<String>,
}
#[derive(Debug, Clone, Deserialize)]
pub struct Node {
    pub id: String,
    pub title: String,
    pub kind: String,
    pub region: String,
    pub source: Source,
    #[serde(default)]
    pub hint: String,
}
#[derive(Debug, Clone, Deserialize)]
pub struct Edge {
    pub from: String,
    pub to: String,
    #[serde(rename = "type")]
    pub kind: String,
    pub origin: String,
    #[serde(default)]
    pub evidence: Vec<Source>,
    #[serde(default)]
    pub label: String,
}
#[derive(Debug, Clone, Deserialize)]
pub struct EdgeType {
    pub relation: String,
}
#[derive(Debug, Clone, Deserialize)]
pub struct Flow {
    version: u32,
    pub title: String,
    pub regions: Vec<Region>,
    pub nodes: Vec<Node>,
    pub edges: Vec<Edge>,
    #[serde(rename = "edgeTypes")]
    pub edge_types: BTreeMap<String, EdgeType>,
}

pub type Positions = BTreeMap<String, (i32, i32)>;

fn array(value: &Value) -> Result<&Vec<Value>> {
    value.as_array().context("Expected array in flow")
}
fn record(value: &Value, fields: &Value) -> Result<Map<String, Value>> {
    if let Some(obj) = value.as_object() {
        return Ok(obj.clone());
    }
    let fields = array(fields)?;
    let values = array(value)?;
    ensure!(
        fields.len() == values.len(),
        "Compact record does not match its field list"
    );
    fields
        .iter()
        .zip(values)
        .map(|(key, value)| {
            Ok((
                key.as_str().context("Expected field name")?.to_owned(),
                value.clone(),
            ))
        })
        .collect()
}

fn expand(data: Value) -> Result<Value> {
    if data["version"] != 2 {
        return Ok(data);
    }
    let defaults = &data["defaults"];
    let mut sources = HashMap::new();
    let mut nodes = vec![];
    for value in array(&data["nodes"])? {
        let mut node = record(value, &data["nodeFields"])?;
        let source = node
            .remove("source")
            .unwrap_or_else(|| defaults["source"].clone());
        let name = source.as_str().context("Missing compact source name")?;
        let mut reference = data["sources"][name]
            .as_object()
            .ok_or_else(|| anyhow!("Unknown compact source: {name}"))?
            .clone();
        reference.insert(
            "line".into(),
            node.remove("line").context("Missing compact source line")?,
        );
        let reference = Value::Object(reference);
        sources.insert(
            node.get("id")
                .and_then(Value::as_str)
                .context("Missing node id")?
                .to_owned(),
            reference.clone(),
        );
        node.insert("source".into(), reference);
        node.entry("region")
            .or_insert_with(|| defaults["region"].clone());
        nodes.push(node);
    }
    let mut edges = vec![];
    for value in array(&data["edges"])? {
        let mut edge = record(value, &data["edgeFields"])?;
        edge.entry("type")
            .or_insert_with(|| defaults["edgeType"].clone());
        edge.entry("origin")
            .or_insert_with(|| defaults["origin"].clone());
        if !edge.contains_key("evidence") {
            ensure!(
                defaults["evidence"] == "endpoint-sources",
                "Compact edge requires evidence or endpoint-sources default"
            );
            let evidence = ["from", "to"]
                .iter()
                .map(|key| {
                    edge.get(*key)
                        .and_then(Value::as_str)
                        .and_then(|s| sources.get(s))
                        .cloned()
                        .context("Dangling compact edge")
                })
                .collect::<Result<Vec<_>>>()?;
            edge.insert("evidence".into(), Value::Array(evidence));
        }
        edges.push(edge);
    }
    Ok(
        json!({"version":1, "title":data["title"], "regions":data["regions"], "edgeTypes":data["edgeTypes"], "nodes":nodes,"edges":edges}),
    )
}

pub fn read(path: &Path, root: &Path) -> Result<(Flow, Positions, Vec<String>)> {
    let bytes = fs::read(path).with_context(|| format!("Read flow {}", path.display()))?;
    parse(&bytes, root)
}
pub fn parse(bytes: &[u8], root: &Path) -> Result<(Flow, Positions, Vec<String>)> {
    let flow: Flow = serde_json::from_value(expand(serde_json::from_slice(bytes)?)?)?;
    let warnings = flow.validate(root)?;
    let positions = flow.layout()?;
    Ok((flow, positions, warnings))
}

impl Flow {
    fn validate(&self, root: &Path) -> Result<Vec<String>> {
        ensure!(self.version == 1, "Expected flow version 1");
        ensure!(!self.nodes.is_empty(), "Flow has no nodes");
        let nodes = self
            .nodes
            .iter()
            .map(|n| (n.id.as_str(), n))
            .collect::<BTreeMap<_, _>>();
        ensure!(nodes.len() == self.nodes.len(), "Duplicate node id");
        let regions = self
            .regions
            .iter()
            .map(|r| (r.id.as_str(), r))
            .collect::<BTreeMap<_, _>>();
        ensure!(!regions.is_empty(), "Flow needs runtime/function regions");
        ensure!(regions.len() == self.regions.len(), "Duplicate region id");
        for region in &self.regions {
            let mut seen = HashSet::new();
            let mut current = Some(region.id.as_str());
            while let Some(id) = current {
                ensure!(seen.insert(id), "Region parent cycle");
                current = regions
                    .get(id)
                    .ok_or_else(|| anyhow!("Unknown region: {id}"))?
                    .parent
                    .as_deref();
            }
        }
        let root = root
            .canonicalize()
            .with_context(|| format!("Missing source checkout: {}", root.display()))?;
        let mut cache: HashMap<PathBuf, (String, usize)> = HashMap::new();
        let mut verify = |source: &Source| -> Result<()> {
            let path = root.join(&source.file);
            let file = path
                .canonicalize()
                .with_context(|| format!("Missing source: {}", path.display()))?;
            ensure!(file.starts_with(&root), "Source escapes checkout");
            if !cache.contains_key(&file) {
                let raw = fs::read(&file)?;
                let lines = std::str::from_utf8(&raw)?.lines().count();
                cache.insert(file.clone(), (format!("{:x}", Sha256::digest(&raw)), lines));
            }
            let (hash, lines) = &cache[&file];
            ensure!(source.sha256 == *hash, "Source changed: {}", file.display());
            ensure!(
                (1..=*lines).contains(&source.line),
                "Invalid line: {}",
                file.display()
            );
            Ok(())
        };
        for node in &self.nodes {
            ensure!(
                matches!(
                    node.kind.as_str(),
                    "call" | "condition" | "throw" | "return" | "step"
                ),
                "Unknown node kind"
            );
            ensure!(
                regions.contains_key(node.region.as_str()),
                "Unknown node region"
            );
            verify(&node.source)?;
        }
        let mut warnings = vec![];
        for edge in &self.edges {
            let from = nodes.get(edge.from.as_str()).context("Dangling edge")?;
            let to = nodes.get(edge.to.as_str()).context("Dangling edge")?;
            let relation = self
                .edge_types
                .get(&edge.kind)
                .context("Undeclared edge type")?
                .relation
                .as_str();
            match edge.origin.as_str() {
                "uncertain" => warnings.push(format!("Uncertain: {} → {}", edge.from, edge.to)),
                "tool" | "agent" => {
                    ensure!(!edge.evidence.is_empty(), "Confirmed edge needs evidence");
                    ensure!(
                        !matches!(relation, "handoff" | "response") || edge.evidence.len() >= 2,
                        "Runtime link needs producer and consumer evidence"
                    );
                }
                _ => bail!("Edge needs origin: tool, agent or uncertain"),
            }
            for source in &edge.evidence {
                verify(source)?;
            }
            match relation {
                "sequence" | "loop" => ensure!(
                    from.region == to.region,
                    "Sequence cannot cross a region; use a call or handoff"
                ),
                "call" => ensure!(
                    regions[to.region.as_str()].parent.as_deref() == Some(&from.region),
                    "Call must go down exactly one function region"
                ),
                "return" => ensure!(
                    regions[from.region.as_str()].parent.as_deref() == Some(&to.region),
                    "Return must go up to its direct caller"
                ),
                "handoff" => ensure!(
                    regions[to.region.as_str()].parent.is_none(),
                    "Handoff target must be a runtime entry region"
                ),
                "response" => {}
                _ => bail!("Unknown layout relation"),
            }
            ensure!(
                !(relation == "sequence" && matches!(from.kind.as_str(), "throw" | "return")),
                "Terminal node cannot continue sequentially"
            );
        }
        Ok(warnings)
    }

    fn layout(&self) -> Result<Positions> {
        let nodes = self
            .nodes
            .iter()
            .map(|n| (n.id.as_str(), n))
            .collect::<BTreeMap<_, _>>();
        let regions = self
            .regions
            .iter()
            .map(|r| (r.id.as_str(), r))
            .collect::<BTreeMap<_, _>>();
        let mut incoming = nodes
            .keys()
            .map(|&k| (k, Vec::new()))
            .collect::<BTreeMap<_, _>>();
        let mut outgoing = incoming.clone();
        for e in &self.edges {
            if self.edge_types[&e.kind].relation != "loop" {
                outgoing
                    .get_mut(e.from.as_str())
                    .unwrap()
                    .push(e.to.as_str());
                incoming
                    .get_mut(e.to.as_str())
                    .unwrap()
                    .push(e.from.as_str());
            }
        }
        let mut degree = incoming
            .iter()
            .map(|(&k, v)| (k, v.len()))
            .collect::<BTreeMap<_, _>>();
        let mut ready = degree
            .iter()
            .filter_map(|(&id, &d)| (d == 0).then_some(id))
            .collect::<BTreeSet<_>>();
        let mut rank = nodes.keys().map(|&k| (k, 0)).collect::<BTreeMap<_, i32>>();
        let mut order = vec![];
        while let Some(id) = ready.pop_first() {
            order.push(id);
            for target in &outgoing[id] {
                rank.insert(target, rank[target].max(rank[id] + 1));
                let d = degree.get_mut(target).unwrap();
                *d -= 1;
                if *d == 0 {
                    ready.insert(target);
                }
            }
        }
        ensure!(
            order.len() == nodes.len(),
            "Cycle found: mark a loop-back edge with relation loop"
        );
        let mut lanes: BTreeMap<&str, i32> = BTreeMap::new();
        for &id in &order {
            let parents = &incoming[id];
            let lane = if parents.len() == 1 && nodes[parents[0]].region == nodes[id].region {
                let siblings = outgoing[parents[0]]
                    .iter()
                    .copied()
                    .collect::<BTreeSet<_>>();
                lanes[parents[0]] + siblings.iter().position(|s| *s == id).unwrap_or(0) as i32
            } else {
                0
            };
            lanes.insert(id, lane);
        }
        let mut region_order = regions.keys().copied().collect::<Vec<_>>();
        region_order.sort_by_key(|&r| {
            (
                nodes
                    .values()
                    .filter(|n| n.region == r)
                    .map(|n| rank[n.id.as_str()])
                    .min()
                    .unwrap_or(0),
                r,
            )
        });
        let mut bases = BTreeMap::new();
        let mut cursor = 80;
        for region in region_order {
            let mut parents = vec![];
            let mut current = Some(region);
            while let Some(r) = current {
                if bases.contains_key(r) {
                    break;
                }
                parents.push(r);
                current = regions[r].parent.as_deref();
            }
            for r in parents.into_iter().rev() {
                bases.insert(r, cursor);
                let max_lane = nodes
                    .values()
                    .filter(|n| n.region == r)
                    .map(|n| lanes[n.id.as_str()])
                    .max()
                    .unwrap_or(0);
                cursor += (max_lane + 1) * 280 + 70;
            }
        }
        let mut positions = Positions::new();
        let mut occupied = BTreeSet::new();
        for id in order {
            let x = 30 + rank[id] * 460;
            let mut y = bases[nodes[id].region.as_str()] + lanes[id] * 280;
            while !occupied.insert((x, y)) {
                y += 280;
            }
            positions.insert(id.into(), (x, y));
        }
        Ok(positions)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (tempfile::TempDir, Value) {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("a.ts"), "call();\n").unwrap();
        let src =
            json!({"file":"a.ts","sha256":format!("{:x}",Sha256::digest(b"call();\n")),"line":1});
        let node = |id: &str| json!({"id":id,"title":id,"kind":"call","region":"api","source":src});
        (
            dir,
            json!({"version":1,"title":"Test","regions":[{"id":"api","label":"API"}],
            "edgeTypes":{"next":{"relation":"sequence"},"task":{"relation":"handoff"}},
            "nodes":[node("a"),node("b")],
            "edges":[{"from":"a","to":"b","type":"next","origin":"agent","evidence":[src]}]}),
        )
    }
    #[test]
    fn order_and_evidence() {
        let (dir, mut data) = fixture();
        let read = |data: &Value| parse(&serde_json::to_vec(data).unwrap(), dir.path());
        let (_, positions, _) = read(&data).unwrap();
        data["nodes"].as_array_mut().unwrap().reverse();
        assert_eq!(read(&data).unwrap().1, positions);
        assert_eq!(positions["b"], (490, 80));
        data["edges"][0]["type"] = json!("task");
        assert!(read(&data)
            .unwrap_err()
            .to_string()
            .contains("producer and consumer"));
        data["edges"][0]["origin"] = json!("uncertain");
        assert_eq!(read(&data).unwrap().2.len(), 1);
        fs::write(dir.path().join("a.ts"), "changed\n").unwrap();
        assert!(read(&data)
            .unwrap_err()
            .to_string()
            .contains("Source changed"));
    }
    #[test]
    fn unmarked_cycles_are_rejected() {
        let (dir, mut data) = fixture();
        data["edges"][0]["to"] = json!("a");
        assert!(parse(&serde_json::to_vec(&data).unwrap(), dir.path())
            .unwrap_err()
            .to_string()
            .contains("Cycle"));
    }
}
