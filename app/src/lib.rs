#![no_std]
extern crate alloc;

use sails_rs::{cell::RefCell, collections::BTreeMap, gstd::{exec::block_height, msg}, prelude::*};

#[derive(Clone, Debug, Default, Encode, Decode, TypeInfo)]
#[codec(crate = sails_rs::scale_codec)]
#[scale_info(crate = sails_rs::scale_info)]
pub struct AgentDna {
    pub name: String,
    pub reliability_score: u32,
    pub call_count: u32,
    pub success_count: u32,
    pub specializations: Vec<String>,
    pub weighted_score: u32,
    pub mutation_count: u32,
    pub last_updated: u32,
}

#[derive(Clone, Debug, Encode, Decode, TypeInfo)]
#[codec(crate = sails_rs::scale_codec)]
#[scale_info(crate = sails_rs::scale_info)]
pub struct Intent {
    pub id: u64,
    pub description: String,
    pub tags: Vec<String>,
    pub category: String,
    pub risk_level: u8,
    pub submitter: ActorId,
    pub status: IntentStatus,
    pub assigned_agent: Option<ActorId>,
    pub submitted_at: u32,
    pub resolved_at: Option<u32>,
}

#[derive(Clone, Debug, Encode, Decode, TypeInfo, PartialEq)]
#[codec(crate = sails_rs::scale_codec)]
#[scale_info(crate = sails_rs::scale_info)]
pub enum IntentStatus {
    Pending,
    Routed,
    Completed,
    Failed,
}

#[derive(Clone, Debug, Encode, Decode, TypeInfo)]
#[codec(crate = sails_rs::scale_codec)]
#[scale_info(crate = sails_rs::scale_info)]
pub struct RankEntry {
    pub agent: ActorId,
    pub name: String,
    pub weighted_score: u32,
    pub call_count: u32,
    pub reliability_score: u32,
}

#[derive(Clone, Debug, Encode, Decode, TypeInfo)]
#[codec(crate = sails_rs::scale_codec)]
#[scale_info(crate = sails_rs::scale_info)]
pub struct RoutingResult {
    pub intent_id: u64,
    pub assigned_agent: ActorId,
    pub agent_name: String,
    pub agent_score: u32,
}

pub struct OnyxState {
    pub owner: ActorId,
    pub agents: BTreeMap<ActorId, AgentDna>,
    pub intents: BTreeMap<u64, Intent>,
    pub intent_counter: u64,
    pub rankings: BTreeMap<String, Vec<ActorId>>,
    pub total_routings: u64,
}

impl OnyxState {
    pub fn new(owner: ActorId) -> Self {
        Self {
            owner,
            agents: BTreeMap::new(),
            intents: BTreeMap::new(),
            intent_counter: 0,
            rankings: BTreeMap::new(),
            total_routings: 0,
        }
    }
}

fn resort_category(state: &mut OnyxState, category: &str) {
    if let Some(list) = state.rankings.get_mut(category) {
        let scores: BTreeMap<ActorId, u32> = state
            .agents
            .iter()
            .map(|(k, v)| (*k, v.weighted_score))
            .collect();
        list.sort_by(|a, b| {
            let sa = scores.get(a).copied().unwrap_or(0);
            let sb = scores.get(b).copied().unwrap_or(0);
            sb.cmp(&sa)
        });
    }
}

pub struct OnyxService<'a> {
    state: &'a RefCell<OnyxState>,
}

#[sails_rs::service]
impl<'a> OnyxService<'a> {
    #[export]
    pub fn register_agent(&mut self, name: String, specializations: Vec<String>) -> bool {
        let caller = msg::source();
        let mut st = self.state.borrow_mut();
        let existing = st.agents.get(&caller).cloned();
        let dna = if let Some(mut prev) = existing {
            for spec in &specializations {
                if !prev.specializations.contains(spec) {
                    prev.specializations.push(spec.clone());
                }
            }
            prev.name = name;
            prev.last_updated = block_height();
            prev
        } else {
            AgentDna {
                name,
                reliability_score: 50,
                weighted_score: 50,
                specializations: specializations.clone(),
                last_updated: block_height(),
                ..Default::default()
            }
        };
        st.agents.insert(caller, dna);
        for spec in specializations {
            let list = st.rankings.entry(spec.clone()).or_default();
            if !list.contains(&caller) {
                list.push(caller);
            }
            resort_category(&mut st, &spec);
        }
        true
    }

    #[export]
    pub fn submit_intent(
        &mut self,
        description: String,
        tags: Vec<String>,
        category: String,
        risk_level: u8,
    ) -> u64 {
        let mut st = self.state.borrow_mut();
        let id = st.intent_counter;
        st.intent_counter += 1;
        st.intents.insert(id, Intent {
            id,
            description,
            tags,
            category,
            risk_level: risk_level.min(2),
            submitter: msg::source(),
            status: IntentStatus::Pending,
            assigned_agent: None,
            submitted_at: block_height(),
            resolved_at: None,
        });
        id
    }

    #[export]
    pub fn route_intent(&mut self, intent_id: u64) -> RoutingResult {
        let mut st = self.state.borrow_mut();
        let intent = st.intents.get(&intent_id).expect("Onyx: intent not found");
        if intent.status != IntentStatus::Pending {
            let agent = intent.assigned_agent.expect("Onyx: inconsistent state");
            let dna = st.agents.get(&agent).cloned().unwrap_or_default();
            return RoutingResult {
                intent_id,
                assigned_agent: agent,
                agent_name: dna.name,
                agent_score: dna.weighted_score,
            };
        }
        let category = intent.category.clone();
        let candidates = st.rankings.get(&category)
            .expect("Onyx: no agents for this category").clone();
        let (best_agent, best_score) = candidates.iter()
            .filter_map(|addr| st.agents.get(addr).map(|dna| (*addr, dna.weighted_score)))
            .max_by_key(|(_, score)| *score)
            .expect("Onyx: no eligible agents");
        let agent_name = st.agents.get(&best_agent)
            .map(|d| d.name.clone()).unwrap_or_default();
        let intent = st.intents.get_mut(&intent_id).unwrap();
        intent.status = IntentStatus::Routed;
        intent.assigned_agent = Some(best_agent);
        if let Some(dna) = st.agents.get_mut(&best_agent) {
            dna.call_count += 1;
        }
        st.total_routings += 1;
        RoutingResult {
            intent_id,
            assigned_agent: best_agent,
            agent_name,
            agent_score: best_score,
        }
    }

    #[export]
    pub fn record_outcome(&mut self, intent_id: u64, success: bool, quality_score: u32) -> bool {
        let mut st = self.state.borrow_mut();
        let quality = quality_score.min(100);
        let (agent_addr, category) = {
            let intent = match st.intents.get(&intent_id) {
                Some(i) => i,
                None => return false,
            };
            if intent.status != IntentStatus::Routed { return false; }
            match intent.assigned_agent {
                Some(a) => (a, intent.category.clone()),
                None => return false,
            }
        };
        if let Some(intent) = st.intents.get_mut(&intent_id) {
            intent.status = if success { IntentStatus::Completed } else { IntentStatus::Failed };
            intent.resolved_at = Some(block_height());
        }
        if let Some(dna) = st.agents.get_mut(&agent_addr) {
            if success { dna.success_count += 1; }
            if dna.call_count > 0 {
                dna.reliability_score = (dna.success_count * 100) / dna.call_count;
            }
            dna.weighted_score = (dna.reliability_score * 70 + quality * 30) / 100;
            dna.mutation_count += 1;
            dna.last_updated = block_height();
        }
        resort_category(&mut st, &category);
        true
    }

    #[export]
    pub fn submit_and_route(
        &mut self,
        description: String,
        tags: Vec<String>,
        category: String,
        risk_level: u8,
    ) -> RoutingResult {
        let id = self.submit_intent(description, tags, category, risk_level);
        self.route_intent(id)
    }
}

pub struct QueryService<'a> {
    state: &'a RefCell<OnyxState>,
}

#[sails_rs::service]
impl<'a> QueryService<'a> {
    #[export]
    pub fn get_agent_dna(&self, address: ActorId) -> Option<AgentDna> {
        self.state.borrow().agents.get(&address).cloned()
    }

    #[export]
    pub fn get_intent(&self, intent_id: u64) -> Option<Intent> {
        self.state.borrow().intents.get(&intent_id).cloned()
    }

    #[export]
    pub fn get_rankings(&self, category: String) -> Vec<RankEntry> {
        let st = self.state.borrow();
        st.rankings.get(&category).map(|list| {
            list.iter().filter_map(|addr| {
                st.agents.get(addr).map(|dna| RankEntry {
                    agent: *addr,
                    name: dna.name.clone(),
                    weighted_score: dna.weighted_score,
                    call_count: dna.call_count,
                    reliability_score: dna.reliability_score,
                })
            }).collect()
        }).unwrap_or_default()
    }

    #[export]
    pub fn get_intent_count(&self) -> u64 { self.state.borrow().intent_counter }

    #[export]
    pub fn get_total_routings(&self) -> u64 { self.state.borrow().total_routings }

    #[export]
    pub fn get_categories(&self) -> Vec<String> {
        self.state.borrow().rankings.keys().cloned().collect()
    }

    #[export]
    pub fn get_all_agents(&self) -> Vec<ActorId> {
        self.state.borrow().agents.keys().cloned().collect()
    }

    #[export]
    pub fn get_top_agents(&self, limit: u32) -> Vec<RankEntry> {
        let st = self.state.borrow();
        let mut all: Vec<RankEntry> = st.agents.iter().map(|(addr, dna)| RankEntry {
            agent: *addr,
            name: dna.name.clone(),
            weighted_score: dna.weighted_score,
            call_count: dna.call_count,
            reliability_score: dna.reliability_score,
        }).collect();
        all.sort_by(|a, b| b.weighted_score.cmp(&a.weighted_score));
        all.truncate(limit as usize);
        all
    }

    #[export]
    pub fn get_recent_intents(&self, limit: u32) -> Vec<Intent> {
        let st = self.state.borrow();
        let mut list: Vec<Intent> = st.intents.values().cloned().collect();
        list.sort_by(|a, b| b.id.cmp(&a.id));
        list.truncate(limit as usize);
        list
    }
}

pub struct AdminService<'a> {
    state: &'a RefCell<OnyxState>,
}

#[sails_rs::service]
impl<'a> AdminService<'a> {
    #[export]
    pub fn remove_agent(&mut self, address: ActorId) -> bool {
        let mut st = self.state.borrow_mut();
        if msg::source() != st.owner { panic!("Onyx: not owner"); }
        if st.agents.remove(&address).is_none() { return false; }
        for list in st.rankings.values_mut() { list.retain(|a| *a != address); }
        true
    }

    #[export]
    pub fn set_agent_score(&mut self, address: ActorId, score: u32) -> bool {
        let mut st = self.state.borrow_mut();
        if msg::source() != st.owner { panic!("Onyx: not owner"); }
        if let Some(dna) = st.agents.get_mut(&address) {
            dna.weighted_score = score.min(100);
            dna.mutation_count += 1;
        } else {
            return false;
        }
        let specs: Vec<String> = st.agents.get(&address)
            .map(|d| d.specializations.clone())
            .unwrap_or_default();
        for spec in specs {
            resort_category(&mut st, &spec);
        }
        true
    }

    #[export]
    pub fn get_owner(&self) -> ActorId { self.state.borrow().owner }
}

pub struct Program {
    state: RefCell<OnyxState>,
}

#[sails_rs::program]
impl Program {
    pub fn new() -> Self {
        Self {
            state: RefCell::new(OnyxState::new(msg::source())),
        }
    }

    pub fn onyx(&self) -> OnyxService<'_> {
        OnyxService { state: &self.state }
    }

    pub fn query(&self) -> QueryService<'_> {
        QueryService { state: &self.state }
    }

    pub fn admin(&self) -> AdminService<'_> {
        AdminService { state: &self.state }
    }
}
